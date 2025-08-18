from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from helios.config import AppConfig
from helios.models import Plan, PlanAction, PricePoint, ForecastPoint


@dataclass
class Inputs:
    prices: List[PricePoint]
    pv_forecast: List[ForecastPoint]
    load_forecast: List[ForecastPoint]


def _align_to_windows(ts: datetime, window_seconds: int) -> datetime:
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % window_seconds), tz=timezone.utc)


def _interpolate_kw(points: List[ForecastPoint], ts: datetime) -> float:
    # nearest hour for now; simple
    nearest = min(points, key=lambda p: abs((p.timestamp - ts).total_seconds()))
    return nearest.value_kw


def _price_at(prices: List[PricePoint], ts: datetime) -> PricePoint:
    nearest = min(prices, key=lambda p: abs((p.timestamp - ts).total_seconds()))
    return nearest


def build_plan(cfg: AppConfig, inputs: Inputs) -> Plan:
    window = cfg.behavior.planning_window_seconds
    horizon = timedelta(hours=cfg.behavior.planning_horizon_hours)
    now = datetime.now(timezone.utc)
    start = _align_to_windows(now, window)
    end = start + horizon

    actions: List[PlanAction] = []

    # Simple heuristic: use arbitrage window above reserve for price arbitrage; else self-consume
    # Compute target grid setpoint per window based on predicted net load and price
    for t in _iter_windows(start, end, window):
        pv_kw = _interpolate_kw(inputs.pv_forecast, t)
        load_kw = _interpolate_kw(inputs.load_forecast, t)
        net_load_kw = max(0.0, load_kw - pv_kw)  # what home needs beyond PV
        pv_surplus_kw = max(0.0, pv_kw - load_kw)
        price = _price_at(inputs.prices, t)

        grid_sp_w = 0
        reason = ""
        battery_target: Optional[float] = None

        # If price very low: charge up to max power within limits
        if price.buy_eur_per_kwh <= sorted(p.buy_eur_per_kwh for p in inputs.prices)[int(0.25 * len(inputs.prices))]:
            grid_sp_w = int(cfg.battery.max_charge_w)
            reason = "Charge from grid (low price)"
        # If price very high: discharge to serve net load and optionally export if allowed
        elif price.buy_eur_per_kwh >= sorted(p.buy_eur_per_kwh for p in inputs.prices)[int(0.75 * len(inputs.prices))]:
            # Aim negative grid setpoint to offset load and possibly export
            export_w = cfg.grid.grid_export_limit_w if cfg.grid.grid_feed_in_enabled else 0
            grid_sp_w = -min(cfg.battery.max_discharge_w, int(net_load_kw * 1000) + export_w)
            reason = "Discharge battery to cover load/export (high price)"
        else:
            # Neutral price: self-consume PV, avoid grid
            if pv_surplus_kw > 0:
                if cfg.grid.grid_feed_in_enabled:
                    grid_sp_w = -min(cfg.grid.grid_export_limit_w, int(pv_surplus_kw * 1000))
                    reason = "Export PV surplus"
                else:
                    grid_sp_w = 0
                    reason = "Curtail PV surplus (no feed-in)"
            else:
                grid_sp_w = int(net_load_kw * 1000)
                reason = "Import to cover deficit"

        # Clamp by grid limits
        grid_sp_w = max(-cfg.grid.grid_export_limit_w, min(cfg.grid.grid_import_limit_w, grid_sp_w))

        actions.append(PlanAction(timestamp=t, grid_setpoint_w=grid_sp_w, battery_target_soc_percent=battery_target, reason=reason))

    return Plan(
        generated_at=datetime.now(timezone.utc),
        valid_from=start,
        valid_to=end,
        window_seconds=window,
        actions=actions,
    )


def _iter_windows(start: datetime, end: datetime, window_seconds: int):
    t = start
    while t < end:
        yield t
        t = t + timedelta(seconds=window_seconds)

