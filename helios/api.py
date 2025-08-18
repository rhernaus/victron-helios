from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
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
from .models import ConfigResponse, Plan, StatusResponse
from .planner import Planner
from .providers import PriceProvider, StubPriceProvider, TibberPriceProvider
from .scheduler import HeliosScheduler
from .state import HeliosState, get_state

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
        return DbusExecutor(dwell=dwell)
    return NoOpExecutor(dwell=dwell)


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

    planner: Planner = state.planner  # type: ignore[assignment]
    plan: Plan = planner.build_plan(price_series=hourly_prices, now=now)
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


def create_app(initial_settings: Optional[HeliosSettings] = None) -> FastAPI:
    app = FastAPI(default_response_class=JSONResponse)

    state = get_state()
    if initial_settings is not None:
        state.settings = initial_settings

    # Initialize planner and scheduler if not already
    if state.planner is None:
        state.planner = Planner(state.settings)
    if state.scheduler is None:
        state.scheduler = HeliosScheduler(state)
    # Ensure dwell uses configured minimum immediately
    state.dwell.minimum_dwell_seconds = state.settings.minimum_action_dwell_seconds
    if state.executor is None:
        state.executor = _select_executor(state.settings, dwell=state.dwell)
    if state.price_provider is None:
        state.price_provider = _select_price_provider(state.settings)

    def recalc_job():
        recalc_job_runs_total.inc()
        _recalc_plan(state)

    def control_job():
        control_job_runs_total.inc()
        _do_control(state)

    @app.on_event("startup")
    def on_startup() -> None:
        scheduler: HeliosScheduler = state.scheduler  # type: ignore[assignment]
        scheduler.start(recalc_job=recalc_job, control_job=control_job)
        # trigger immediate first plan
        recalc_job()

    @app.on_event("shutdown")
    def on_shutdown() -> None:
        scheduler: HeliosScheduler = state.scheduler  # type: ignore[assignment]
        scheduler.shutdown()

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/pause")
    def pause() -> StatusResponse:
        with state.lock:
            state.automation_paused = True
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
                # update dwell controller with new minimum dwell
                state.dwell.minimum_dwell_seconds = new_settings.minimum_action_dwell_seconds
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
                # reschedule with new intervals
                scheduler: HeliosScheduler = state.scheduler  # type: ignore[assignment]
                scheduler.reschedule(recalc_job=recalc_job, control_job=control_job)
                return ConfigResponse(data=state.settings.to_public_dict())
        except Exception as exc:  # validation or other issues
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/status", response_model=StatusResponse)
    def status() -> StatusResponse:
        with state.lock:
            return StatusResponse(
                automation_paused=state.automation_paused,
                last_recalc_at=state.last_recalc_at,
                last_control_at=state.last_control_at,
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

    return app
