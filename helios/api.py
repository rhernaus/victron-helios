from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Response, Query
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import ConfigUpdate, HeliosSettings
from .executor import DbusExecutor, Executor, NoOpExecutor
from .metrics import (
    automation_paused,
    control_job_runs_total,
    control_ticks_total,
    current_setpoint_watts,
    plan_age_seconds,
    planner_runs_total,
    recalc_job_runs_total,
)
from .models import Action, ConfigResponse, Plan, StatusResponse
from .planner import Planner
from .providers import (
    PriceProvider,
    StubPriceProvider,
    TibberPriceProvider,
    ForecastProvider,
    StubForecastProvider,
    OpenWeatherForecastProvider,
)
from .scheduler import HeliosScheduler
from .telemetry import DbusTelemetryReader, NoOpTelemetryReader, TelemetrySnapshot
from .state import HeliosState, get_state

try:  # optional dependency for local telemetry storage
    import sqlite3  # type: ignore
except Exception:  # pragma: no cover - optional
    sqlite3 = None  # type: ignore[assignment]
from contextlib import closing

logger = logging.getLogger("helios")


def _select_price_provider(settings: HeliosSettings) -> PriceProvider:
    if settings.price_provider == "tibber" and settings.tibber_token:
        return TibberPriceProvider(
            access_token=settings.tibber_token,
            home_id=settings.tibber_home_id,
        )
    return StubPriceProvider()


def _select_executor(settings: HeliosSettings, dwell) -> Executor:
    if settings.executor_backend == "dbus":
        return DbusExecutor(dwell=dwell, settings=settings)
    return NoOpExecutor(dwell=dwell)


def _select_telemetry_reader(settings: HeliosSettings):
    if getattr(settings, "telemetry_backend", "noop") == "dbus":
        return DbusTelemetryReader()
    return NoOpTelemetryReader()


def _select_forecast_provider(settings: HeliosSettings) -> ForecastProvider:
    # Use OpenWeather if API key and location are configured; otherwise stub
    use_ow = (
        settings.openweather_api_key
        and settings.location_lat is not None
        and settings.location_lon is not None
    )
    if use_ow:
        return OpenWeatherForecastProvider(
            api_key=str(settings.openweather_api_key),
            lat=float(settings.location_lat or 0.0),
            lon=float(settings.location_lon or 0.0),
            pv_peak_watts=float(settings.pv_peak_watts or 4000.0),
        )
    return StubForecastProvider(peak_watts=float(settings.pv_peak_watts or 4000.0))


def _warn_if_backend_missing_deps(settings: HeliosSettings) -> None:
    """Log clear warnings if a selected backend depends on modules that are not importable.

    Today this checks for dbus-python when executor or telemetry backends are set to 'dbus'.
    """
    # dbus for executor
    try:
        if settings.executor_backend == "dbus":  # pragma: no cover - environment dependent
            try:
                import dbus  # type: ignore
            except Exception as exc:
                logger.warning(
                    "Executor backend 'dbus' selected but 'dbus' module is not importable: %s. "
                    "If running inside a virtual environment on Venus OS, recreate the venv with "
                    "--system-site-packages so the system 'dbus-python' is visible.",
                    exc,
                )
    except Exception:
        # Never break app startup on diagnostics
        pass

    # dbus for telemetry
    try:
        if getattr(settings, "telemetry_backend", "noop") == "dbus":  # pragma: no cover
            try:
                import dbus  # type: ignore
            except Exception as exc:
                logger.warning(
                    "Telemetry backend 'dbus' selected but 'dbus' module is not importable: %s. "
                    "If running inside a virtual environment on Venus OS, recreate the venv with "
                    "--system-site-packages so the system 'dbus-python' is visible.",
                    exc,
                )
    except Exception:
        pass


def _recalc_plan(state: HeliosState) -> None:
    # Retrieve price curve using provider abstraction
    now = datetime.now(timezone.utc)
    start_hour = now.replace(minute=0, second=0, microsecond=0)

    # Determine provider and horizon atomically; do not hold the lock for network calls
    with state.lock:
        settings_snapshot = state.settings
        current_provider: PriceProvider | None = state.price_provider

        # Decide if we need a new provider based on settings
        desired_is_tibber = settings_snapshot.price_provider == "tibber" and bool(
            settings_snapshot.tibber_token
        )
        needs_new = False
        if current_provider is None:
            needs_new = True
        elif desired_is_tibber and not isinstance(current_provider, TibberPriceProvider):
            needs_new = True
        elif not desired_is_tibber and not isinstance(current_provider, StubPriceProvider):
            needs_new = True
        elif desired_is_tibber and isinstance(current_provider, TibberPriceProvider):
            # Replace if token changed
            if current_provider.access_token != settings_snapshot.tibber_token:
                needs_new = True

        if needs_new:
            provider_to_use: PriceProvider = _select_price_provider(settings_snapshot)
            state.price_provider = provider_to_use
        else:
            # current_provider is not None here by construction
            provider_to_use = current_provider  # type: ignore[assignment]

        horizon_hours = settings_snapshot.planning_horizon_hours

    # Fetch prices outside the lock
    hourly_prices = provider_to_use.get_prices(
        start_hour, start_hour + timedelta(hours=horizon_hours)
    )
    # Forecasts (optional)
    try:
        forecast = state.forecast_provider or _select_forecast_provider(settings_snapshot)
    except Exception:
        forecast = None
    solar_fc = (
        forecast.get_solar_forecast(start_hour, start_hour + timedelta(hours=horizon_hours))
        if forecast
        else None
    )
    load_fc = (
        forecast.get_load_forecast(start_hour, start_hour + timedelta(hours=horizon_hours))
        if forecast
        else None
    )

    planner: Planner = state.planner  # type: ignore[assignment]
    plan: Plan = planner.build_plan(
        price_series=hourly_prices,
        now=now,
        solar_forecast=solar_fc,
        load_forecast=load_fc,
    )
    with state.lock:
        state.latest_plan = plan
        state.last_recalc_at = now
    planner_runs_total.inc()
    plan_age_seconds.set(0)


def _do_control(state: HeliosState) -> None:
    now = datetime.now(timezone.utc)
    # Skip if paused
    with state.lock:
        state.last_control_at = now
        paused = state.automation_paused
        plan = state.latest_plan
    automation_paused.set(1 if paused else 0)
    if paused:
        return
    if plan is None:
        return
    # Apply via executor abstraction
    executor: Executor = state.executor or NoOpExecutor(dwell=state.dwell)
    executor.apply_setpoint(now, plan)
    slot = plan.slot_for(now)
    if slot is not None:
        current_setpoint_watts.set(slot.target_grid_setpoint_w)
    control_ticks_total.inc()


def create_app(initial_settings: Optional[HeliosSettings] = None) -> FastAPI:  # noqa: C901
    app = FastAPI(default_response_class=JSONResponse)

    state = get_state()
    if initial_settings is not None:
        state.settings = initial_settings
    else:
        # Attempt to load persisted settings (sanitized) and overlay on defaults
        loaded = HeliosSettings.load_from_disk(HeliosSettings().data_dir)
        if loaded:
            try:
                merged = {**state.settings.model_dump(), **loaded}
                state.settings = HeliosSettings.model_validate(merged)
            except Exception as exc:
                # Provide actionable diagnostics so users can see exactly why
                # the persisted settings could not be applied on startup.
                try:
                    loaded_keys = list(loaded.keys())
                except Exception:
                    loaded_keys = []
                logger.exception(
                    "Failed to load settings from disk at startup; using defaults. "
                    "error=%s loaded_keys=%s",
                    exc,
                    loaded_keys,
                )

    # Initialize planner and scheduler if not already
    if state.planner is None:
        state.planner = Planner(state.settings)
    if state.scheduler is None:
        state.scheduler = HeliosScheduler(state)
    # Ensure dwell uses configured minimum immediately
    state.dwell.minimum_dwell_seconds = state.settings.minimum_action_dwell_seconds
    # Configure per-action dwell mapping
    state.dwell.per_action_dwell_seconds = {
        Action.CHARGE_FROM_GRID: (
            state.settings.dwell_seconds_charge_from_grid
            if state.settings.dwell_seconds_charge_from_grid is not None
            else state.settings.minimum_action_dwell_seconds
        ),
        Action.DISCHARGE_TO_LOAD: (
            state.settings.dwell_seconds_discharge_to_load
            if state.settings.dwell_seconds_discharge_to_load is not None
            else state.settings.minimum_action_dwell_seconds
        ),
        Action.EXPORT_TO_GRID: (
            state.settings.dwell_seconds_export_to_grid
            if state.settings.dwell_seconds_export_to_grid is not None
            else state.settings.minimum_action_dwell_seconds
        ),
        Action.IDLE: (
            state.settings.dwell_seconds_idle
            if state.settings.dwell_seconds_idle is not None
            else state.settings.minimum_action_dwell_seconds
        ),
    }
    if state.executor is None:
        state.executor = _select_executor(state.settings, dwell=state.dwell)
    if state.price_provider is None:
        state.price_provider = _select_price_provider(state.settings)
    if state.telemetry_reader is None:
        state.telemetry_reader = _select_telemetry_reader(state.settings)
    if getattr(state, "forecast_provider", None) is None:
        state.forecast_provider = _select_forecast_provider(state.settings)

    def recalc_job():
        recalc_job_runs_total.inc()
        _recalc_plan(state)

    def control_job():
        control_job_runs_total.inc()
        _do_control(state)

    def telemetry_job():
        try:
            reader = state.telemetry_reader or NoOpTelemetryReader()
            snap = reader.read()
            with state.lock:
                state.last_telemetry = snap
            # Persist a rolling sample if DB available and data_dir writable
            if sqlite3 is not None:
                try:
                    db_path = Path(state.settings.data_dir) / "telemetry.db"
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                    with closing(sqlite3.connect(str(db_path))) as conn:  # type: ignore[union-attr]
                        conn.execute(
                            "CREATE TABLE IF NOT EXISTS telemetry ("
                            "ts INTEGER PRIMARY KEY, "
                            "soc REAL, load INTEGER, solar INTEGER)"
                        )
                        conn.execute(
                            "CREATE TABLE IF NOT EXISTS prices ("
                            "ts INTEGER PRIMARY KEY, raw REAL)"
                        )
                        conn.execute(
                            (
                                "INSERT OR REPLACE INTO telemetry("
                                "ts, soc, load, solar) VALUES (?, ?, ?, ?)"
                            ),
                            (
                                int(datetime.now(timezone.utc).timestamp()),
                                snap.soc_percent if snap.soc_percent is not None else None,
                                snap.load_w if snap.load_w is not None else None,
                                snap.solar_w if snap.solar_w is not None else None,
                            ),
                        )
                        conn.commit()
                except Exception as db_exc:
                    logger.debug("Telemetry DB write failed: %s", db_exc)
        except Exception as exc:
            # Keep last snapshot but record the failure for diagnostics
            logger.warning("Telemetry read failed: %s", exc)

    @app.on_event("startup")
    def on_startup() -> None:
        # Proactively warn about missing optional dependencies for selected backends
        try:
            _warn_if_backend_missing_deps(state.settings)
        except Exception:
            pass
        scheduler: HeliosScheduler = state.scheduler  # type: ignore[assignment]
        scheduler.start(recalc_job=recalc_job, control_job=control_job, telemetry_job=telemetry_job)
        # trigger immediate first plan
        recalc_job()
        # prime telemetry once quickly
        telemetry_job()

    @app.on_event("shutdown")
    def on_shutdown() -> None:
        scheduler: HeliosScheduler = state.scheduler  # type: ignore[assignment]
        scheduler.shutdown()
        # As a safety measure, reset grid setpoint to 0 on shutdown if using D-Bus executor
        try:
            with state.lock:
                executor = state.executor
            if isinstance(executor, DbusExecutor):
                import dbus  # type: ignore

                bus = dbus.SystemBus()
                proxy = bus.get_object(
                    "com.victronenergy.settings", "/Settings/CGwacs/AcPowerSetPoint"
                )
                try:
                    iface = dbus.Interface(proxy, dbus_interface="com.victronenergy.BusItem")
                    iface.SetValue(0)
                except Exception:
                    props = dbus.Interface(proxy, dbus_interface="org.freedesktop.DBus.Properties")
                    props.Set("com.victronenergy.BusItem", "Value", 0)
        except Exception as exc:
            logger.warning("Failed to reset grid setpoint on shutdown: %s", exc)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/recalc")
    def force_recalc() -> dict:
        _recalc_plan(state)
        return {"status": "ok"}

    @app.post("/pause")
    def pause() -> StatusResponse:
        with state.lock:
            state.automation_paused = True
            executor = state.executor
        # Safety: if using D-Bus, reset grid setpoint to 0 on pause
        try:
            if isinstance(executor, DbusExecutor):
                import dbus  # type: ignore

                bus = dbus.SystemBus()
                proxy = bus.get_object(
                    "com.victronenergy.settings", "/Settings/CGwacs/AcPowerSetPoint"
                )
                try:
                    iface = dbus.Interface(proxy, dbus_interface="com.victronenergy.BusItem")
                    iface.SetValue(0)
                except Exception:
                    props = dbus.Interface(proxy, dbus_interface="org.freedesktop.DBus.Properties")
                    props.Set("com.victronenergy.BusItem", "Value", 0)
        except Exception as exc:
            logger.warning("Failed to reset grid setpoint on pause: %s", exc)
        return status()

    @app.post("/resume")
    def resume() -> StatusResponse:
        with state.lock:
            state.automation_paused = False
        return status()

    @app.get("/metrics")
    def metrics() -> Response:
        data = generate_latest()
        # Return a raw Response with the correct content type
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)

    @app.get("/telemetry/history")
    def telemetry_history(
        limit: int = 500,
        from_: Optional[str] = Query(default=None, alias="from"),
        to: Optional[str] = Query(default=None, alias="to"),
    ) -> dict:
        """Return recent telemetry rows from the local SQLite store.

        This is a simple built-in time-series store to bootstrap forecasting.
        """

        def _parse_ts(q: Optional[str]) -> Optional[int]:
            if q is None:
                return None
            try:
                v = int(q)
                # support ms
                if v > 10**12:
                    v = v // 1000
                return v
            except Exception:
                try:
                    dt = datetime.fromisoformat(q.replace("Z", "+00:00"))
                    return int(dt.timestamp())
                except Exception:
                    return None

        fr = _parse_ts(from_)
        to_ts = _parse_ts(to)

        try:
            db_path = Path(state.settings.data_dir) / "telemetry.db"
            with closing(sqlite3.connect(str(db_path))) as conn:
                if fr is not None and to_ts is not None:
                    rows = list(
                        conn.execute(
                            (
                                "SELECT ts, soc, load, solar FROM telemetry "
                                "WHERE ts >= ? AND ts <= ? ORDER BY ts ASC"
                            ),
                            (fr, to_ts),
                        )
                    )
                else:
                    rows = list(
                        conn.execute(
                            "SELECT ts, soc, load, solar FROM telemetry ORDER BY ts DESC LIMIT ?",
                            (int(max(1, min(10000, limit))),),
                        )
                    )
        except Exception:
            rows = []
        if fr is None or to_ts is None:
            rows.reverse()
        items = []
        for r in rows:
            items.append(
                {
                    "t": datetime.fromtimestamp(r[0], tz=timezone.utc).isoformat(),
                    "soc": r[1],
                    "load": r[2],
                    "solar": r[3],
                }
            )
        return {"items": items}

    @app.get("/config", response_model=ConfigResponse)
    def get_config() -> ConfigResponse:
        with state.lock:
            return ConfigResponse(data=state.settings.to_public_dict())

    @app.put("/config", response_model=ConfigResponse)
    def update_config(update: ConfigUpdate) -> ConfigResponse:
        # Apply atomically and validate via HeliosSettings
        try:
            with state.lock:
                new_settings = update.apply_to(state.settings)
                state.settings = new_settings
                # update dwell controller with new minimum dwell and per-action dwell
                state.dwell.minimum_dwell_seconds = new_settings.minimum_action_dwell_seconds
                state.dwell.per_action_dwell_seconds = {
                    Action.CHARGE_FROM_GRID: (
                        new_settings.dwell_seconds_charge_from_grid
                        if new_settings.dwell_seconds_charge_from_grid is not None
                        else new_settings.minimum_action_dwell_seconds
                    ),
                    Action.DISCHARGE_TO_LOAD: (
                        new_settings.dwell_seconds_discharge_to_load
                        if new_settings.dwell_seconds_discharge_to_load is not None
                        else new_settings.minimum_action_dwell_seconds
                    ),
                    Action.EXPORT_TO_GRID: (
                        new_settings.dwell_seconds_export_to_grid
                        if new_settings.dwell_seconds_export_to_grid is not None
                        else new_settings.minimum_action_dwell_seconds
                    ),
                    Action.IDLE: (
                        new_settings.dwell_seconds_idle
                        if new_settings.dwell_seconds_idle is not None
                        else new_settings.minimum_action_dwell_seconds
                    ),
                }
                # swap executor if backend changed
                state.executor = _select_executor(new_settings, dwell=state.dwell)
                # swap provider only if selection, token or home changed
                # preserve cache when configuration remains stable
                current_provider = state.price_provider
                desired_provider = _select_price_provider(new_settings)
                swap = False
                if type(current_provider) is not type(desired_provider):
                    swap = True
                elif isinstance(current_provider, TibberPriceProvider) and isinstance(
                    desired_provider, TibberPriceProvider
                ):
                    if (
                        current_provider.access_token != desired_provider.access_token
                        or current_provider.home_id != desired_provider.home_id
                    ):
                        swap = True
                if swap or current_provider is None:
                    state.price_provider = desired_provider
                # swap telemetry reader
                state.telemetry_reader = _select_telemetry_reader(new_settings)
                # Warn if selected backends require missing dependencies
                try:
                    _warn_if_backend_missing_deps(new_settings)
                except Exception:
                    pass
                # reschedule with new intervals
                scheduler: HeliosScheduler = state.scheduler  # type: ignore[assignment]
                scheduler.reschedule(
                    recalc_job=recalc_job, control_job=control_job, telemetry_job=telemetry_job
                )
                # persist non-secret settings snapshot to disk
                try:
                    state.settings.persist_to_disk()
                except Exception:
                    logger.warning("Failed to persist settings to disk")
                return ConfigResponse(data=state.settings.to_public_dict())
        except Exception as exc:  # validation or other issues
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/status", response_model=StatusResponse)
    def status() -> StatusResponse:
        with state.lock:
            # Try to derive current slot and reasoning
            current_action = None
            current_setpoint = None
            current_reason = None
            if state.latest_plan is not None:
                slot = state.latest_plan.slot_for(datetime.now(timezone.utc))
                if slot is not None:
                    current_action = slot.action
                    current_setpoint = slot.target_grid_setpoint_w
                    current_reason = getattr(slot, "reason", None)

            tel: TelemetrySnapshot = state.last_telemetry
            return StatusResponse(
                automation_paused=state.automation_paused,
                last_recalc_at=state.last_recalc_at,
                last_control_at=state.last_control_at,
                current_action=current_action,
                current_setpoint_w=current_setpoint,
                current_reason=current_reason,
                soc_percent=tel.soc_percent,
                load_w=tel.load_w,
                solar_w=tel.solar_w,
                ev_charger_status=tel.ev_status,
            )

    @app.get("/plan")
    def get_plan() -> dict:
        with state.lock:
            if state.latest_plan is None:
                raise HTTPException(status_code=404, detail="Plan not ready")
            plan_age_seconds.set(
                (datetime.now(timezone.utc) - state.latest_plan.generated_at).total_seconds()
            )
            return state.latest_plan.model_dump()

    @app.get("/prices")
    def get_prices(
        from_: Optional[str] = Query(default=None, alias="from"),
        to: Optional[str] = Query(default=None, alias="to"),
    ) -> dict:  # pragma: no cover - exercised via UI, not tests
        """Return the current planning horizon price series and derived buy/sell prices.

        The response schema is intentionally simple for the web UI and not versioned.
        """
        # Snapshot settings and provider without holding the lock across network calls
        with state.lock:
            settings_snapshot = state.settings
            provider_to_use: PriceProvider | None = state.price_provider
        if provider_to_use is None:
            provider_to_use = _select_price_provider(settings_snapshot)
            with state.lock:
                state.price_provider = provider_to_use

        now = datetime.now(timezone.utc)

        # Parse optional range params (epoch seconds or ISO). Default to planning horizon
        def _parse_ts(q: Optional[str]) -> Optional[int]:
            if q is None:
                return None
            try:
                v = int(q)
                if v > 10**12:
                    v = v // 1000
                return v
            except Exception:
                try:
                    dt = datetime.fromisoformat(q.replace("Z", "+00:00"))
                    return int(dt.timestamp())
                except Exception:
                    return None

        fr = _parse_ts(from_)
        to_ts = _parse_ts(to)

        if fr is None or to_ts is None:
            start_hour = now.replace(minute=0, second=0, microsecond=0)
            horizon_hours = settings_snapshot.planning_horizon_hours
            start_dt = start_hour
            end_dt = start_hour + timedelta(hours=horizon_hours)
        else:
            start_dt = datetime.fromtimestamp(fr, tz=timezone.utc).replace(
                minute=0, second=0, microsecond=0
            )
            # inclusive end; extend to cover final hour
            end_dt = datetime.fromtimestamp(to_ts, tz=timezone.utc).replace(
                minute=0, second=0, microsecond=0
            ) + timedelta(hours=1)

        # Prepare local store if available and query existing rows
        rows_from_db: list[tuple[int, float]] = []
        db_path = Path(state.settings.data_dir) / "telemetry.db"
        if sqlite3 is not None:
            try:
                with closing(sqlite3.connect(str(db_path))) as conn:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS prices (ts INTEGER PRIMARY KEY, raw REAL)"
                    )
                    cur = conn.execute(
                        "SELECT ts, raw FROM prices WHERE ts >= ? AND ts < ? ORDER BY ts ASC",
                        (int(start_dt.timestamp()), int(end_dt.timestamp())),
                    )
                    rows_from_db = list(cur)
            except Exception as db_exc:
                logger.debug("Price DB read failed: %s", db_exc)

        have_map = {ts: raw for (ts, raw) in rows_from_db}

        # Fetch from provider for the requested range, then backfill DB for missing rows
        raw_series = provider_to_use.get_prices(start_dt, end_dt)
        if sqlite3 is not None:
            try:
                with closing(sqlite3.connect(str(db_path))) as conn:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS prices (ts INTEGER PRIMARY KEY, raw REAL)"
                    )
                    for ts_dt, raw in raw_series:
                        ts = int(ts_dt.replace(minute=0, second=0, microsecond=0).timestamp())
                        if ts not in have_map:
                            try:
                                conn.execute(
                                    ("INSERT OR REPLACE INTO prices(ts, raw) " "VALUES (?, ?)"),
                                    (ts, float(raw)),
                                )
                            except Exception as db_write_exc:
                                logger.debug("Skipping price row write: %s", db_write_exc)
                    conn.commit()
                    cur = conn.execute(
                        "SELECT ts, raw FROM prices WHERE ts >= ? AND ts < ? ORDER BY ts ASC",
                        (int(start_dt.timestamp()), int(end_dt.timestamp())),
                    )
                    rows_from_db = list(cur)
            except Exception as db_exc:
                logger.debug("Price DB write failed: %s", db_exc)
        # Fallback to provider series if DB not available
        if not rows_from_db:
            rows_from_db = [
                (
                    int(ts_dt.replace(minute=0, second=0, microsecond=0).timestamp()),
                    float(raw),
                )
                for ts_dt, raw in raw_series
            ]

        def to_buy_sell(raw: float) -> tuple[float, float]:
            buy = (
                raw * settings_snapshot.buy_price_multiplier
                + settings_snapshot.buy_price_fixed_fee_eur_per_kwh
            )
            sell = (
                raw * settings_snapshot.sell_price_multiplier
                - settings_snapshot.sell_price_fixed_deduction_eur_per_kwh
            )
            return buy, sell

        items: list[dict] = []
        for ts_epoch, raw_val in rows_from_db:
            ts_dt = datetime.fromtimestamp(int(ts_epoch), tz=timezone.utc)
            buy, sell = to_buy_sell(float(raw_val))
            items.append(
                {
                    "t": ts_dt.isoformat(),
                    "raw": round(float(raw_val), 6),
                    "buy": round(float(buy), 6),
                    "sell": round(float(sell), 6),
                }
            )

        return {"generated_at": now.isoformat(), "start": start_dt.isoformat(), "items": items}

    @app.get("/export")
    def export_series() -> dict:
        """Export prices, flows and costs for the current plan window.

        Returns a compact JSON with aligned time slots for downstream analysis.
        """
        with state.lock:
            plan = state.latest_plan
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not ready")

        # prices for window
        try:
            settings_snapshot = state.settings
            provider = state.price_provider or _select_price_provider(settings_snapshot)
            start = plan.slots[0].start if plan.slots else plan.generated_at
            end = plan.slots[-1].end if plan.slots else plan.generated_at
            price_series = provider.get_prices(start, end)
        except Exception:
            price_series = []

        slot_items = []
        for s in plan.slots:
            slot_items.append(
                {
                    "start": s.start.isoformat(),
                    "end": s.end.isoformat(),
                    "action": s.action,
                    "setpoint_w": s.target_grid_setpoint_w,
                    "flows": {
                        "solar_to_grid_kwh": s.solar_to_grid_kwh,
                        "solar_to_battery_kwh": s.solar_to_battery_kwh,
                        "solar_to_usage_kwh": s.solar_to_usage_kwh,
                        "battery_to_grid_kwh": s.battery_to_grid_kwh,
                        "battery_to_usage_kwh": s.battery_to_usage_kwh,
                        "grid_to_usage_kwh": s.grid_to_usage_kwh,
                        "grid_to_battery_kwh": s.grid_to_battery_kwh,
                    },
                    "costs": {
                        "grid_cost_eur": s.grid_cost_eur,
                        "grid_savings_eur": s.grid_savings_eur,
                        "battery_cost_eur": s.battery_cost_eur,
                    },
                }
            )

        prices = [{"t": t.isoformat(), "raw": p} for (t, p) in price_series]

        return {
            "plan_generated_at": plan.generated_at.isoformat(),
            "slots": slot_items,
            "prices": prices,
        }

    # --- Web UI mounting ---
    try:
        web_dir = Path(__file__).resolve().parent / "web"
        if web_dir.exists():
            app.mount("/ui", StaticFiles(directory=str(web_dir), html=True), name="ui")

            @app.get("/")
            def root_redirect() -> RedirectResponse:
                return RedirectResponse(url="/ui/")

    except Exception:
        # If static mounting fails for any reason, continue serving API only
        logger.warning("Web UI mounting failed; continuing without UI")

    return app
