from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from helios.config import AppConfig
from helios.dbus_client import VictronDbusClient
from helios.models import Plan


class Controller:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._dbus = VictronDbusClient()
        self._plan: Optional[Plan] = None
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        try:
            await self._dbus.connect()
        except Exception:
            pass
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
            self._task = None

    def update_plan(self, plan: Plan) -> None:
        self._plan = plan

    async def _run(self) -> None:
        interval = max(1, self.cfg.behavior.dbus_update_interval_seconds)
        while True:
            await asyncio.sleep(interval)
            await self._tick()

    async def _tick(self) -> None:
        if not self._plan:
            return
        now = datetime.now(timezone.utc)
        # Find the most recent or current action
        actions = [a for a in self._plan.actions if a.timestamp <= now]
        if not actions:
            action = self._plan.actions[0]
        else:
            action = max(actions, key=lambda a: a.timestamp)
        try:
            await self._dbus.set_grid_setpoint_w(action.grid_setpoint_w)
        except Exception:
            pass

