from __future__ import annotations

import asyncio
from typing import Optional

try:
    from dbus_next.aio import MessageBus
    from dbus_next import BusType
    from dbus_next.message import Message
    from dbus_next.constants import MessageType
except Exception:  # pragma: no cover - dev systems without dbus
    MessageBus = None  # type: ignore
    BusType = None  # type: ignore
    Message = None  # type: ignore
    MessageType = None  # type: ignore


class VictronDbusClient:
    def __init__(self) -> None:
        self._bus: Optional[MessageBus] = None

    async def connect(self) -> None:
        if MessageBus is None:
            return
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    async def set_grid_setpoint_w(self, watts: int) -> None:
        # Venus OS: com.victronenergy settings path for grid setpoint
        # /Settings/CGwacs/AcPowerSetPoint
        if self._bus is None:
            return
        msg = Message(
            destination="com.victronenergy.settings",
            path="/",
            interface="com.victronenergy.BusItem",
            member="SetValue",
            signature="v",
            body=[watts],
        )
        await self._bus.call(msg)

