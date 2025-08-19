from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
import logging

from .config import HeliosSettings
from .dwell import DwellController
from .metrics import executor_apply_failures_total, executor_apply_seconds
from .models import Plan

logger = logging.getLogger("helios")


class Executor(ABC):
    @abstractmethod
    def apply_setpoint(self, when: datetime, plan: Plan) -> None:
        """Apply the setpoint for the given time instant based on the plan."""


@dataclass
class NoOpExecutor(Executor):
    dwell: DwellController | None = None

    def apply_setpoint(self, when: datetime, plan: Plan) -> None:
        slot = plan.slot_for(when)
        if slot is None:
            return
        if self.dwell is not None and not self.dwell.should_change(slot.action, when):
            return
        if self.dwell is not None:
            self.dwell.note_action(slot.action, when)
        with executor_apply_seconds.time():
            logger.info(
                "NoOpExecutor applying setpoint W=%s action=%s at=%s",
                slot.target_grid_setpoint_w,
                slot.action.value,
                when.isoformat(),
            )


@dataclass
class DbusExecutor(Executor):
    dwell: DwellController | None = None
    settings: HeliosSettings | None = None

    def apply_setpoint(self, when: datetime, plan: Plan) -> None:
        slot = plan.slot_for(when)
        if slot is None:
            return
        if self.dwell is not None and not self.dwell.should_change(slot.action, when):
            return
        if self.dwell is not None:
            self.dwell.note_action(slot.action, when)
        # Real D-Bus integration
        try:
            with executor_apply_seconds.time():
                target = slot.target_grid_setpoint_w
                # Clamp by settings limits if provided
                if self.settings is not None:
                    if self.settings.grid_import_limit_w is not None:
                        target = min(target, self.settings.grid_import_limit_w)
                    if self.settings.grid_export_limit_w is not None:
                        target = max(target, -self.settings.grid_export_limit_w)
                    # Also clamp by battery charge/discharge limits
                    if target > 0 and self.settings.battery_charge_limit_w is not None:
                        target = min(target, self.settings.battery_charge_limit_w)
                    if target < 0 and self.settings.battery_discharge_limit_w is not None:
                        target = max(target, -self.settings.battery_discharge_limit_w)
                try:
                    import dbus  # type: ignore

                    bus = dbus.SystemBus()
                    proxy = bus.get_object(
                        "com.victronenergy.settings", "/Settings/CGwacs/AcPowerSetPoint"
                    )
                    try:
                        iface = dbus.Interface(proxy, dbus_interface="com.victronenergy.BusItem")
                        iface.SetValue(int(target))
                    except Exception:
                        props = dbus.Interface(
                            proxy, dbus_interface="org.freedesktop.DBus.Properties"
                        )
                        props.Set("com.victronenergy.BusItem", "Value", int(target))
                    logger.info("DbusExecutor set grid setpoint to %s W", int(target))
                except Exception as dbus_exc:  # nosec B112
                    logger.error("D-Bus write failed: %s", dbus_exc)
                    raise
        except Exception:
            executor_apply_failures_total.inc()
            # Best-effort failsafe: attempt to set grid setpoint to 0 to hand control back to ESS
            try:
                import dbus  # type: ignore

                bus = dbus.SystemBus()
                proxy = bus.get_object(
                    "com.victronenergy.settings", "/Settings/CGwacs/AcPowerSetPoint"
                )
                try:
                    iface = dbus.Interface(proxy, dbus_interface="com.victronenergy.BusItem")
                    iface.SetValue(0)
                except Exception:
                    props = dbus.Interface(
                        proxy, dbus_interface="org.freedesktop.DBus.Properties"
                    )
                    props.Set("com.victronenergy.BusItem", "Value", 0)
            except Exception:  # nosec B112
                pass
            raise
