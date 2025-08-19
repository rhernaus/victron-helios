from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
import logging

from .config import HeliosSettings
from .dwell import DwellController
from .metrics import (
    executor_apply_failures_total,
    executor_apply_seconds,
    executor_misapplies_total,
    executor_reasserts_total,
)
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
    _last_setpoint_w: int | None = None

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
                    # Apply optional ramping to avoid abrupt steps
                    ramp = self.settings.grid_ramp_w_per_second
                    if ramp is not None and self._last_setpoint_w is not None:
                        # Bound change by ramp * update interval seconds
                        interval = max(1, int(self.settings.dbus_update_interval_seconds))
                        max_delta = max(0, ramp) * interval
                        delta = target - self._last_setpoint_w
                        if abs(delta) > max_delta:
                            target = self._last_setpoint_w + (max_delta if delta > 0 else -max_delta)
                try:
                    import dbus  # type: ignore

                    # Connect to SystemBus with basic retry to handle transient bus issues
                    _bus_attempts = 0
                    _bus: any = None
                    _proxy: any = None
                    _max_bus_attempts = 1 + (self.settings.dbus_write_retries if self.settings else 0)
                    while _bus_attempts < _max_bus_attempts:
                        try:
                            _bus = dbus.SystemBus()
                            _proxy = _bus.get_object(
                                "com.victronenergy.settings", "/Settings/CGwacs/AcPowerSetPoint"
                            )
                            break
                        except Exception:
                            _bus_attempts += 1
                            if self.settings and self.settings.dbus_write_retry_delay_seconds > 0:
                                import time as _time

                                _time.sleep(self.settings.dbus_write_retry_delay_seconds)
                    if _proxy is None:
                        raise RuntimeError("Failed to connect to D-Bus proxy for grid setpoint")
                    proxy = _proxy
                    # Write with retry/backoff strategy
                    write_retries = 0
                    retry_delay = 0.0
                    if self.settings is not None:
                        write_retries = max(0, int(self.settings.dbus_write_retries))
                        retry_delay = max(0.0, float(self.settings.dbus_write_retry_delay_seconds))
                    attempt = 0
                    while True:
                        try:
                            iface = dbus.Interface(
                                proxy, dbus_interface="com.victronenergy.BusItem"
                            )
                            iface.SetValue(int(target))
                            break
                        except Exception:
                            props = dbus.Interface(
                                proxy, dbus_interface="org.freedesktop.DBus.Properties"
                            )
                            try:
                                props.Set("com.victronenergy.BusItem", "Value", int(target))
                                break
                            except Exception as _:
                                if attempt >= write_retries:
                                    raise
                                attempt += 1
                                if retry_delay > 0:
                                    import time as _time

                                    _time.sleep(retry_delay)
                    logger.info("DbusExecutor set grid setpoint to %s W", int(target))
                    self._last_setpoint_w = int(target)

                    # Optional verify and reassert loop
                    attempts = 0
                    max_attempts = 0
                    delay_seconds = 0.0
                    if self.settings is not None:
                        max_attempts = max(0, int(self.settings.dbus_reassert_attempts))
                        delay_seconds = max(0.0, float(self.settings.dbus_write_retry_delay_seconds))
                    # Try to read back the value and re-assert if mismatch
                    while attempts < max_attempts:
                        attempts += 1
                        try:
                            # Read back via BusItem if available
                            props = dbus.Interface(
                                proxy, dbus_interface="org.freedesktop.DBus.Properties"
                            )
                            current = int(
                                props.Get("com.victronenergy.BusItem", "Value")  # type: ignore
                            )
                            if int(current) == int(target):
                                break
                            executor_reasserts_total.inc()
                            # Re-assert
                            try:
                                iface = dbus.Interface(
                                    proxy, dbus_interface="com.victronenergy.BusItem"
                                )
                                iface.SetValue(int(target))
                            except Exception:
                                props.Set("com.victronenergy.BusItem", "Value", int(target))
                            if delay_seconds > 0:
                                import time as _time

                                _time.sleep(delay_seconds)
                        except Exception:
                            # If read-back fails, break out; we'll rely on single write
                            break
                    else:
                        # If loop exhausted without break and mismatch persists, count misapply
                        try:
                            props = dbus.Interface(
                                proxy, dbus_interface="org.freedesktop.DBus.Properties"
                            )
                            current = int(
                                props.Get("com.victronenergy.BusItem", "Value")  # type: ignore
                            )
                            if int(current) != int(target):
                                executor_misapplies_total.inc()
                        except Exception:
                            pass
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
