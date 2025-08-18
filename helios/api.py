from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import ORJSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import ConfigUpdate, HeliosSettings
from .executor import DbusExecutor, Executor, NoOpExecutor
from .metrics import (
    automation_paused,
    control_ticks_total,
    current_setpoint_watts,
    planner_runs_total,
)
from .models import ConfigResponse, Plan, StatusResponse
from .planner import Planner
from .providers import PriceProvider, StubPriceProvider, TibberPriceProvider
from .scheduler import HeliosScheduler
from .state import HeliosState, get_state

logger = logging.getLogger("helios")


def _select_price_provider(settings: HeliosSettings) -> PriceProvider:
    if settings.price_provider == "tibber" and settings.tibber_token:
        return TibberPriceProvider(access_token=settings.tibber_token)
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
        provider: PriceProvider | None = state.price_provider

        # Decide if we need a new provider based on settings
        desired_is_tibber = settings_snapshot.price_provider == "tibber" and bool(
            settings_snapshot.tibber_token
        )
        needs_new = False
        if provider is None:
            needs_new = True
        elif desired_is_tibber and not isinstance(provider, TibberPriceProvider):
            needs_new = True
        elif not desired_is_tibber and not isinstance(provider, StubPriceProvider):
            needs_new = True
        elif desired_is_tibber and isinstance(provider, TibberPriceProvider):
            # Replace if token changed
            if provider.access_token != settings_snapshot.tibber_token:
                needs_new = True

        if needs_new:
            provider = _select_price_provider(settings_snapshot)
            state.price_provider = provider

        horizon_hours = settings_snapshot.planning_horizon_hours

    # Fetch prices outside the lock
    hourly_prices = provider.get_prices(start_hour, start_hour + timedelta(hours=horizon_hours))

    planner: Planner = state.planner  # type: ignore[assignment]
    plan: Plan = planner.build_plan(price_series=hourly_prices, now=now)
    with state.lock:
        state.latest_plan = plan
        state.last_recalc_at = now
    planner_runs_total.inc()


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
    app = FastAPI(default_response_class=ORJSONResponse)

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
        _recalc_plan(state)

    def control_job():
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
    def metrics() -> ORJSONResponse:
        data = generate_latest()
        # Return a raw Response with the correct content type
        from fastapi import Response

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
                # swap provider if selection or token changed
                state.price_provider = _select_price_provider(new_settings)
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
            return state.latest_plan.model_dump()

    return app
