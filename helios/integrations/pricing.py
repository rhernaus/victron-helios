from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import httpx

from helios.config import AppConfig
from helios.models import PricePoint


TIBBER_PRICE_URL = "https://api.tibber.com/v1-beta/gql"


TIBBER_QUERY = {
    "query": """
    { viewer { homes { currentSubscription { priceInfo { today { total energy startsAt } tomorrow { total energy startsAt } } } } } }
    """
}


async def fetch_prices(cfg: AppConfig) -> List[PricePoint]:
    token = cfg.pricing.tibber_api_token
    if not token:
        return _synthetic_prices(cfg)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(TIBBER_PRICE_URL, json=TIBBER_QUERY, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    try:
        homes = data["data"]["viewer"]["homes"]
        price_info = homes[0]["currentSubscription"]["priceInfo"]
        points = (price_info.get("today", []) or []) + (price_info.get("tomorrow", []) or [])
    except Exception:
        return _synthetic_prices(cfg)

    results: List[PricePoint] = []
    for p in points:
        ts = datetime.fromisoformat(p["startsAt"].replace("Z", "+00:00")).astimezone(timezone.utc)
        raw = float(p.get("energy") or p.get("total") or 0.0)
        results.append(_price_point_from_raw(cfg, ts, raw))
    return results


def _price_point_from_raw(cfg: AppConfig, ts: datetime, raw: float) -> PricePoint:
    buy = raw * cfg.pricing.buy_price_multiplier + cfg.pricing.buy_price_additive_eur_per_kwh
    sell = max(0.0, raw * cfg.pricing.sell_price_multiplier - cfg.pricing.sell_price_subtractive_eur_per_kwh)
    return PricePoint(timestamp=ts, price_eur_per_kwh_raw=raw, buy_eur_per_kwh=buy, sell_eur_per_kwh=sell)


def _synthetic_prices(cfg: AppConfig) -> List[PricePoint]:
    now = datetime.now(timezone.utc)
    start = now.replace(minute=0, second=0, microsecond=0)
    prices: List[PricePoint] = []
    for h in range(0, 48):
        ts = start + timedelta(hours=h)
        base = 0.12 + 0.10 * (0.5 + 0.5 * __import__("math").sin((h - 6) / 24 * 3.14159 * 2))
        prices.append(_price_point_from_raw(cfg, ts, base))
    return prices

