from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException

from helios.config import AppConfig, load_config, save_config
from helios.controller import Controller
from helios.integrations.pricing import fetch_prices
from helios.integrations.solar import forecast_pv
from helios.integrations.consumption import forecast_load
from helios.models import Plan
from helios.planner import Inputs, build_plan
from helios.config import _to_dict


class HeliosAppState:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.controller = Controller(cfg)
        self.current_plan: Optional[Plan] = None
        self._planner_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await self.controller.start()
        if self._planner_task is None:
            self._planner_task = asyncio.create_task(self._planner_loop())

    async def stop(self) -> None:
        if self._planner_task:
            self._planner_task.cancel()
            try:
                await self._planner_task
            except Exception:
                pass
            self._planner_task = None
        await self.controller.stop()

    async def _planner_loop(self) -> None:
        while True:
            try:
                await self._rebuild_plan()
            except Exception:
                pass
            await asyncio.sleep(max(60, self.cfg.behavior.recalculation_interval_seconds))

    async def _rebuild_plan(self) -> None:
        prices, pv, load = await asyncio.gather(
            fetch_prices(self.cfg),
            forecast_pv(self.cfg),
            forecast_load(),
        )
        inputs = Inputs(prices=prices, pv_forecast=pv, load_forecast=load)
        plan = build_plan(self.cfg, inputs)
        self.current_plan = plan
        self.controller.update_plan(plan)


def create_app() -> FastAPI:
    app = FastAPI(title="Helios", version="0.1.0")
    state = HeliosAppState(load_config())

    @app.on_event("startup")
    async def _startup() -> None:
        await state.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await state.stop()

    @app.get("/")
    async def root():
        return {
            "name": "Helios",
            "time": datetime.now(timezone.utc).isoformat(),
            "plan_generated_at": state.current_plan.generated_at.isoformat() if state.current_plan else None,
        }

    @app.get("/api/plan")
    async def get_plan():
        if not state.current_plan:
            raise HTTPException(404, "Plan not available yet")
        plan = state.current_plan
        return {
            "generated_at": plan.generated_at.isoformat(),
            "valid_from": plan.valid_from.isoformat(),
            "valid_to": plan.valid_to.isoformat(),
            "window_seconds": plan.window_seconds,
            "actions": [
                {
                    "timestamp": a.timestamp.isoformat(),
                    "grid_setpoint_w": a.grid_setpoint_w,
                    "battery_target_soc_percent": a.battery_target_soc_percent,
                    "reason": a.reason,
                }
                for a in plan.actions
            ],
        }

    @app.get("/api/config")
    async def get_config():
        return _to_dict(state.cfg)

    @app.post("/api/config")
    async def update_config(new_cfg: dict):
        from helios.config import _from_dict

        try:
            cfg = _from_dict(new_cfg)
        except Exception as e:
            raise HTTPException(400, f"Invalid config: {e}")
        state.cfg = cfg
        state.controller.cfg = cfg
        save_config(cfg)
        return {"ok": True}

    return app

