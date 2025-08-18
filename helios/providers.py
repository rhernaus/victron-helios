from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import time

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .metrics import (
    price_provider_failures_total,
    price_provider_request_seconds,
    price_provider_requests_total,
)


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
    _cache: dict[str, tuple[datetime, list[tuple[datetime, float]]]] | None = None
    home_id: str | None = None
    _rate_tokens: float = 2.0
    _rate_last_refill: float = 0.0
    _rate_capacity: float = 2.0
    _rate_per_second: float = 1 / 10.0  # 1 request per 10s

    def __post_init__(self) -> None:  # dataclass post-init
        if self._cache is None:
            self._cache = {}
        self._rate_last_refill = time.monotonic()

    def _cache_key(self) -> str:
        suffix = self.home_id or "default"
        return f"prices_today_tomorrow::{suffix}"

    def _get_cached(self) -> list[tuple[datetime, float]] | None:
        key = self._cache_key()
        cache = self._cache or {}
        entry = cache.get(key)
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
        if self._cache is None:
            self._cache = {}
        self._cache[self._cache_key()] = (next_day, data)

    def _rate_limit(self) -> None:
        now = time.monotonic()
        elapsed = max(0.0, now - self._rate_last_refill)
        self._rate_last_refill = now
        self._rate_tokens = min(
            self._rate_capacity, self._rate_tokens + elapsed * self._rate_per_second
        )
        if self._rate_tokens < 1.0:
            # Sleep just enough to accumulate one token
            needed = 1.0 - self._rate_tokens
            delay = needed / self._rate_per_second
            time.sleep(min(delay, 2.0))
            self._rate_tokens = 0.0
        else:
            self._rate_tokens -= 1.0

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
            with price_provider_request_seconds.labels(provider="tibber").time():
                try:
                    self._rate_limit()
                    with httpx.Client(timeout=10) as client:
                        resp = client.post(url, json=query, headers=headers)
                        resp.raise_for_status()
                        data = resp.json()
                    price_provider_requests_total.labels(provider="tibber", result="success").inc()
                except httpx.HTTPError:
                    price_provider_requests_total.labels(provider="tibber", result="error").inc()
                    price_provider_failures_total.inc()
                    raise
            homes = data.get("data", {}).get("viewer", {}).get("homes", [])
            if not homes:
                return []
            # Optional: choose home by ID if provided
            home = None
            if self.home_id:
                for h in homes:
                    if h.get("id") == self.home_id:
                        home = h
                        break
            if home is None:
                home = homes[0]
            price_info = home.get("currentSubscription", {}).get("priceInfo", {})
            series = []
            sections = (price_info.get("today", []) or []) + (price_info.get("tomorrow", []) or [])
            for section in sections:
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
