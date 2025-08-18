from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from helios.models import ForecastPoint


async def forecast_load() -> List[ForecastPoint]:
    # Synthetic: baseline + morning/evening peaks
    now = datetime.now(timezone.utc)
    start = now.replace(minute=0, second=0, microsecond=0)
    results: List[ForecastPoint] = []
    for h in range(0, 48):
        ts = start + timedelta(hours=h)
        hour = ts.hour
        base = 0.5  # kW
        peak_morning = 1.0 if 6 <= hour <= 9 else 0.0
        peak_evening = 1.2 if 17 <= hour <= 22 else 0.0
        results.append(ForecastPoint(timestamp=ts, value_kw=base + peak_morning + peak_evening))
    return results

