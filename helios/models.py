from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional


@dataclass
class TimeSlot:
    start: datetime
    end: datetime


@dataclass
class PricePoint:
    timestamp: datetime
    price_eur_per_kwh_raw: float
    buy_eur_per_kwh: float
    sell_eur_per_kwh: float


@dataclass
class ForecastPoint:
    timestamp: datetime
    value_kw: float


@dataclass
class PlanAction:
    timestamp: datetime
    grid_setpoint_w: int
    battery_target_soc_percent: Optional[float]
    reason: str


@dataclass
class Plan:
    generated_at: datetime
    valid_from: datetime
    valid_to: datetime
    window_seconds: int
    actions: List[PlanAction]


@dataclass
class RealtimeStatus:
    timestamp: datetime
    grid_power_w: int
    pv_power_w: int
    load_power_w: int
    battery_soc_percent: float
    battery_power_w: int

