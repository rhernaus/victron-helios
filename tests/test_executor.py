from datetime import datetime, timedelta, timezone
import types

from helios.config import HeliosSettings
from helios.dwell import DwellController
from helios.executor import DbusExecutor, NoOpExecutor
from helios.models import Action, Plan, PlanSlot


def make_plan(setpoint_w: int) -> Plan:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    slot = PlanSlot(
        start=now - timedelta(seconds=1),
        end=now + timedelta(seconds=10),
        action=Action.CHARGE_FROM_GRID if setpoint_w >= 0 else Action.EXPORT_TO_GRID,
        target_grid_setpoint_w=setpoint_w,
    )
    return Plan(generated_at=now, planning_window_seconds=10, slots=[slot])


def test_noop_executor_updates_dwell_state():
    dwell = DwellController(minimum_dwell_seconds=60)
    exec_noop = NoOpExecutor(dwell=dwell)
    plan = make_plan(500)
    now = datetime.now(timezone.utc)
    # First apply should be permitted and should update dwell state
    exec_noop.apply_setpoint(now, plan)
    assert dwell.last_action == Action.CHARGE_FROM_GRID


class DummyBusItem:
    def __init__(self, proxy):
        self._proxy = proxy

    def SetValue(self, value: int) -> None:  # noqa: N802 (external API name)
        self._proxy._value = int(value)


class DummyProps:
    def __init__(self, proxy, reassert_mismatch_once: bool = False):
        self._proxy = proxy
        self._get_calls = 0
        self._mismatch_once = reassert_mismatch_once

    def Set(self, iface: str, prop: str, value: int) -> None:  # noqa: N802
        if iface == "com.victronenergy.BusItem" and prop == "Value":
            self._proxy._value = int(value)

    def Get(self, iface: str, prop: str) -> int:  # noqa: N802
        self._get_calls += 1
        if (
            self._mismatch_once
            and self._get_calls == 1
            and iface == "com.victronenergy.BusItem"
            and prop == "Value"
        ):
            # Return an incorrect value to trigger reassert path once
            return int(self._proxy._value) + 1
        return int(self._proxy._value)


class DummyProxy:
    def __init__(self, reassert_mismatch_once: bool = False):
        self._value = 0
        self._reassert_mismatch_once = reassert_mismatch_once

    def _make_interface(self, dbus_interface: str):
        if dbus_interface == "com.victronenergy.BusItem":
            return DummyBusItem(self)
        if dbus_interface == "org.freedesktop.DBus.Properties":
            return DummyProps(self, reassert_mismatch_once=self._reassert_mismatch_once)
        raise ValueError("unknown interface")


class DummyBus:
    def __init__(self, proxy: DummyProxy):
        self._proxy = proxy

    def get_object(self, service: str, path: str):  # noqa: D401
        return self._proxy


def make_dbus_module(proxy: DummyProxy):
    mod = types.SimpleNamespace()

    def SystemBus():  # noqa: N802 (external API name)
        return DummyBus(proxy)

    def Interface(proxy_obj, dbus_interface: str):  # noqa: N802
        return proxy_obj._make_interface(dbus_interface)

    mod.SystemBus = SystemBus
    mod.Interface = Interface
    return mod


def test_dbusexecutor_clamp_and_ramp(monkeypatch):
    # Prepare dummy dbus module
    proxy = DummyProxy()
    dbus_mod = make_dbus_module(proxy)
    monkeypatch.setitem(__import__("sys").modules, "dbus", dbus_mod)

    settings = HeliosSettings(
        grid_import_limit_w=2000,
        battery_charge_limit_w=500,
        grid_ramp_w_per_second=100,
        dbus_update_interval_seconds=10,
        executor_backend="dbus",
    )
    exec_dbus = DbusExecutor(dwell=DwellController(), settings=settings)
    exec_dbus._last_setpoint_w = 0  # start from 0W

    plan = make_plan(3000)
    now = datetime.now(timezone.utc)
    exec_dbus.apply_setpoint(now, plan)
    # Expected: clamp by battery limit (500 W) before ramping => 500 W
    assert proxy._value == 500


def test_dbusexecutor_reassert(monkeypatch):
    # Dummy dbus where first read-back mismatches to trigger reassert
    proxy = DummyProxy(reassert_mismatch_once=True)
    dbus_mod = make_dbus_module(proxy)
    monkeypatch.setitem(__import__("sys").modules, "dbus", dbus_mod)

    settings = HeliosSettings(
        grid_import_limit_w=1500,
        dbus_reassert_attempts=2,
        dbus_write_retry_delay_seconds=0.0,
        executor_backend="dbus",
    )
    exec_dbus = DbusExecutor(dwell=DwellController(), settings=settings)

    plan = make_plan(1200)
    now = datetime.now(timezone.utc)
    exec_dbus.apply_setpoint(now, plan)
    # After reassert, the proxy value must equal target
    assert proxy._value == 1200
