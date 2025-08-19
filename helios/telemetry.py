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

    def _sum_phases(self, bus, service: str, base: str) -> int | float | None:  # type: ignore[no-untyped-def]
        total = 0.0
        found = False
        for phase in ("L1", "L2", "L3"):
            v = self._read_value(bus, service, f"{base}/{phase}/Power")
            if isinstance(v, (int, float)):
                total += float(v)
                found = True
        return int(total) if found else None

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

            # Load (W): prefer 3ph sum, fall back to aggregate path
            load = self._sum_phases(bus, system_service, "/Ac/Consumption")
            if load is None:
                load = self._read_value(bus, system_service, "/Ac/Consumption/Power")
            if isinstance(load, (int, float)):
                snap.load_w = int(load)

            # Solar (W): sum AC PV on output and on grid phases; include DC PV
            pv_out = self._sum_phases(bus, system_service, "/Ac/PvOnOutput")
            pv_grid = self._sum_phases(bus, system_service, "/Ac/PvOnGrid")
            pv_dc = self._read_value(bus, system_service, "/Dc/Pv/Power")
            total_pv = 0.0
            for v in (pv_out, pv_grid, pv_dc if isinstance(pv_dc, (int, float)) else None):
                if isinstance(v, (int, float)):
                    total_pv += float(v)
            snap.solar_w = int(total_pv) if total_pv else None

            # EV Charger: detect any service under com.victronenergy.evcharger.*
            try:
                names = bus.list_names()
            except Exception:
                names = []
            ev_status: dict = {}
            for name in names:
                if not str(name).startswith("com.victronenergy.evcharger."):
                    continue
                # read a superset of common fields; tolerate device-specific variants
                candidates = [
                    "/Mode",
                    "/Status",  # common on evchargers
                    "/State",  # some models
                    "/StartStop",
                    "/Connected",
                    "/Charging",
                    "/Ac/Power",
                    "/Power",
                    "/Ac/Current",
                    "/Current",
                    "/SetCurrent",
                    "/MaxCurrent",
                    "/ChargingTime",
                    "/ProductName",
                    "/FirmwareVersion",
                    "/Serial",
                ]
                for p in candidates:
                    val = self._read_value(bus, name, p)
                    if val is not None and val != []:
                        ev_status[p] = val
                if ev_status:
                    break
            snap.ev_status = ev_status or None
            return snap
        except Exception:
            # If dbus not available or any failure, return empty snapshot
            return TelemetrySnapshot()
