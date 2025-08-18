from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


class PriceProvider(ABC):
    @abstractmethod
    def get_prices(self, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        """Return (timestamp, raw_price) pairs in UTC for the interval [start, end)."""


class ForecastProvider(ABC):
    @abstractmethod
    def get_solar_forecast(self, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        """Return (timestamp, expected_watts) for solar generation forecast."""

    @abstractmethod
    def get_load_forecast(self, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        """Return (timestamp, expected_watts) for household load forecast."""


class EVProvider(ABC):
    @abstractmethod
    def get_status(self) -> dict:
        """Return EV status (SoC, charging state, at_home)."""

    @abstractmethod
    def start_charging(self) -> None: ...

    @abstractmethod
    def stop_charging(self) -> None: ...


@dataclass
class StubPriceProvider(PriceProvider):
    swing_low: float = 0.15
    swing_high: float = 0.35

    def get_prices(self, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        """Hourly sawtooth prices between swing_low and swing_high for stubbing."""
        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)
        hours = int((end - start).total_seconds() // 3600)
        base = (self.swing_high + self.swing_low) / 2.0
        amplitude = (self.swing_high - self.swing_low) / 2.0
        series: list[tuple[datetime, float]] = []
        for h in range(hours + 1):
            t = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=h)
            phase = (h % 24) / 24.0
            price = base + amplitude * (2 * phase - 1)
            series.append((t, round(price, 4)))
        return series


@dataclass
class TibberPriceProvider(PriceProvider):
    access_token: str
    _cache: Dict[str, Tuple[datetime, list[tuple[datetime, float]]]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:  # dataclass post-init
        if self._cache is None:
            self._cache = {}

    def _cache_key(self) -> str:
        return "prices_today_tomorrow"

    def _get_cached(self) -> list[tuple[datetime, float]] | None:
        key = self._cache_key()
        entry = self._cache.get(key)
        if not entry:
            return None
        expires_at, data = entry
        # expire cache slightly after tomorrow midnight UTC
        if datetime.now(timezone.utc) < expires_at:
            return data
        return None

    def _set_cache(self, data: list[tuple[datetime, float]]) -> None:
        # set expiry at next day 03:00 UTC to be safe
        now = datetime.now(timezone.utc)
        next_day = (now + timedelta(days=1)).replace(hour=3, minute=0, second=0, microsecond=0)
        self._cache[self._cache_key()] = (next_day, data)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    def get_prices(self, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        # Minimal Tibber GraphQL query for current home prices
        # Note: In a full implementation, select the correct home and timezone handling.
        query = {
            "query": """
            query {
              viewer {
                homes {
                  currentSubscription {
                    priceInfo {
                      today { total startsAt }
                      tomorrow { total startsAt }
                    }
                  }
                }
              }
            }
            """
        }
        headers = {"Authorization": f"Bearer {self.access_token}"}
        url = "https://api.tibber.com/v1-beta/gql"
        cached = self._get_cached()
        if cached is None:
            with httpx.Client(timeout=10) as client:
                resp = client.post(url, json=query, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            homes = data.get("data", {}).get("viewer", {}).get("homes", [])
            if not homes:
                return []
            price_info = homes[0].get("currentSubscription", {}).get("priceInfo", {})
            series = []
            for section in (price_info.get("today", []) or []) + (price_info.get("tomorrow", []) or []):
                starts_at = section.get("startsAt")
                total = section.get("total")
                if starts_at is None or total is None:
                    continue
                try:
                    ts = datetime.fromisoformat(starts_at.replace("Z", "+00:00")).astimezone(
                        timezone.utc
                    )
                except Exception:  # nosec B112
                    # Skip bad entries but keep processing other price points
                    continue
                series.append((ts, float(total)))
            # Sort and cache full day horizon
            series.sort(key=lambda p: p[0])
            self._set_cache(series)
        else:
            series = cached
        # Filter to [start, end)
        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)
        series = [p for p in series if start <= p[0] < end]
        return series


@dataclass
class StubForecastProvider(ForecastProvider):
    def get_solar_forecast(self, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        # Flat zero for now
        return []

    def get_load_forecast(self, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        # Flat zero for now
        return []


@dataclass
class StubEVProvider(EVProvider):
    soc_percent: float = 50.0
    charging: bool = False
    at_home: bool = True

    def get_status(self) -> dict:
        return {"soc_percent": self.soc_percent, "charging": self.charging, "at_home": self.at_home}

    def start_charging(self) -> None:
        self.charging = True

    def stop_charging(self) -> None:
        self.charging = False
