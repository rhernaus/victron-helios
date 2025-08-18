from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import ORJSONResponse
import logging

from .config import ConfigUpdate, HeliosSettings
from .models import ConfigResponse, Plan, StatusResponse
from .planner import Planner
from .scheduler import HeliosScheduler
from .state import HeliosState, get_state

logger = logging.getLogger("helios")


def _recalc_plan(state: HeliosState) -> None:
    # Stub price curve: 48h hourly prices with a gentle daily swing
    now = datetime.now(timezone.utc)
    start_hour = now.replace(minute=0, second=0, microsecond=0)
    hourly_prices: list[tuple[datetime, float]] = []
    for h in range(0, 48):
        t = start_hour + timedelta(hours=h)
        # simple 24h sawtooth between 0.15 and 0.35 EUR/kWh
        base = 0.25
        amplitude = 0.10
        phase = (h % 24) / 24.0
        price = base + amplitude * (2 * phase - 1)
        hourly_prices.append((t, round(price, 4)))

    planner: Planner = state.planner  # type: ignore[assignment]
    plan: Plan = planner.build_plan(price_series=hourly_prices, now=now)
    with state.lock:
        state.latest_plan = plan
        state.last_recalc_at = now


def _do_control(state: HeliosState) -> None:
    now = datetime.now(timezone.utc)
    with state.lock:
        state.last_control_at = now
        plan = state.latest_plan
        if plan is None:
            return
        slot = plan.slot_for(now)
        if slot is None:
            return
        setpoint = slot.target_grid_setpoint_w
        action = slot.action
    # Outside lock, perform side-effects (would be D-Bus calls). Here we just log.
    logger.info(
        "Control tick %s: applying setpoint W=%s action=%s",
        now.isoformat(),
        setpoint,
        action.value,
    )


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
                # reschedule with new intervals
                scheduler: HeliosScheduler = state.scheduler  # type: ignore[assignment]
                scheduler.reschedule(recalc_job=recalc_job, control_job=control_job)
                return ConfigResponse(data=state.settings.to_public_dict())
        except Exception as exc:  # validation or other issues
            raise HTTPException(status_code=400, detail=str(exc))

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
