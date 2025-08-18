from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import httpx

from helios.config import AppConfig
from helios.models import ForecastPoint


async def forecast_pv(cfg: AppConfig) -> List[ForecastPoint]:
    # If Solcast configured, try it, else synthetic bell-shaped production around noon
    if cfg.location.solcast_api_key and cfg.location.solcast_site_id:
        try:
            return await _solcast_forecast(cfg)
        except Exception:
            pass
    return _synthetic_pv(cfg)


async def _solcast_forecast(cfg: AppConfig) -> List[ForecastPoint]:
    api_key = cfg.location.solcast_api_key
    site_id = cfg.location.solcast_site_id
    url = f"https://api.solcast.com.au/rooftop_sites/{site_id}/forecasts?format=json&api_key={api_key}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    forecasts = data.get("forecasts", [])
    results: List[ForecastPoint] = []
    for f in forecasts:
        ts = datetime.fromisoformat(f["period_end"].replace("Z", "+00:00")).astimezone(timezone.utc)
        kw = float(f.get("pv_estimate", 0.0))
        results.append(ForecastPoint(timestamp=ts, value_kw=kw))
    return results


def _synthetic_pv(cfg: AppConfig) -> List[ForecastPoint]:
    now = datetime.now(timezone.utc)
    start = now.replace(minute=0, second=0, microsecond=0)
    results: List[ForecastPoint] = []
    peak_kw = 5.0
    for h in range(0, 48):
        ts = start + timedelta(hours=h)
        hour = (ts.hour + ts.minute / 60.0)
        # Simple day curve: 0 at night, bell curve centered at 13:00
        import math
        distance = abs((hour - 13) / 5.5)
        factor = max(0.0, 1.0 - distance ** 2)
        results.append(ForecastPoint(timestamp=ts, value_kw=peak_kw * factor))
    return results

