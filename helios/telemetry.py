from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class TelemetrySnapshot:
    soc_percent: Optional[float] = None
    load_w: Optional[int] = None
    solar_w: Optional[int] = None
    ev_status: Optional[dict] = None


class TelemetryReader:
    def read(self) -> TelemetrySnapshot:  # pragma: no cover - interface
        raise NotImplementedError


class NoOpTelemetryReader(TelemetryReader):
    def read(self) -> TelemetrySnapshot:  # pragma: no cover - trivial
        return TelemetrySnapshot()


class DbusTelemetryReader(TelemetryReader):  # pragma: no cover - hardware specific
    """Best-effort D-Bus ingestion for Victron devices.

    Reads a small set of aggregated values if available, falling back gracefully
    when services or paths are missing.
    """

    def __init__(self) -> None:
        pass

    def _read_value(self, bus, service: str, path: str):  # type: ignore[no-untyped-def]
        try:
            proxy = bus.get_object(service, path)
            props = __import__("dbus").Interface(
                proxy, dbus_interface="org.freedesktop.DBus.Properties"
            )
            val = props.Get("com.victronenergy.BusItem", "Value")  # type: ignore[attr-defined]
            try:
                return int(val)
            except Exception:
                try:
                    return float(val)
                except Exception:
                    return val
        except Exception:
            return None

    def read(self) -> TelemetrySnapshot:  # type: ignore[override]
        try:
            import dbus  # type: ignore

            bus = dbus.SystemBus()
            snap = TelemetrySnapshot()

            # Prefer aggregated values from com.victronenergy.system if present
            system_service = "com.victronenergy.system"
            # Battery SoC (%): /Dc/Battery/Soc
            soc = self._read_value(bus, system_service, "/Dc/Battery/Soc")
            if isinstance(soc, (int, float)):
                snap.soc_percent = float(soc)

            # Load (W): /Ac/Consumption/Power
            load = self._read_value(bus, system_service, "/Ac/Consumption/Power")
            if isinstance(load, (int, float)):
                snap.load_w = int(load)

            # Solar (W): aggregate AC PV and DC PV if available
            pv_ac = self._read_value(bus, system_service, "/Ac/PvOnGrid/Power")
            pv_dc = self._read_value(bus, system_service, "/Dc/Pv/Power")
            total_pv = 0
            if isinstance(pv_ac, (int, float)):
                total_pv += int(pv_ac)
            if isinstance(pv_dc, (int, float)):
                total_pv += int(pv_dc)
            snap.solar_w = total_pv if total_pv != 0 else None

            # EV Charger: any evcharger service
            try:
                names = bus.list_names()
            except Exception:
                names = []
            ev_status: dict = {}
            for name in names:
                if not str(name).startswith("com.victronenergy.evcharger"):
                    continue
                fields = [
                    "/Mode",
                    "/State",
                    "/Enabled",
                    "/Connected",
                    "/Charging",
                    "/Power",
                    "/Current",
                ]
                for p in fields:
                    val = self._read_value(bus, name, p)
                    if val is not None:
                        ev_status[p] = val
                if ev_status:
                    break
            snap.ev_status = ev_status or None
            return snap
        except Exception:
            # If dbus not available or any failure, return empty snapshot
            return TelemetrySnapshot()
