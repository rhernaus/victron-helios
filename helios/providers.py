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


@dataclass
class OpenWeatherForecastProvider(ForecastProvider):
    api_key: str
    lat: float
    lon: float
    pv_peak_watts: float = 4000.0

    def _hours(self, start: datetime, end: datetime) -> list[datetime]:
        start = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        end = end.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        hours = int(max(0, (end - start).total_seconds() // 3600))
        return [start + timedelta(hours=h) for h in range(hours + 1)]

    def _owm_hourly(self) -> list[dict]:
        # One Call API 3.0: include hourly forecast for next 48h
        url = f"https://api.openweathermap.org/data/3.0/onecall?lat={self.lat}&lon={self.lon}&appid={self.api_key}&units=metric&exclude=minutely,current,daily,alerts"
        with httpx.Client(timeout=10) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
        return data.get("hourly", [])

    def _estimate_pv_from_clouds(self, cloud_percent: float) -> float:
        # Simple mapping: clear sky ~ 1.0, fully overcast ~ 0.15 of peak
        cloud = max(0.0, min(100.0, float(cloud_percent))) / 100.0
        factor = max(0.15, 1.0 - 0.85 * cloud)
        return self.pv_peak_watts * factor

    def get_solar_forecast(self, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        try:
            hourly = self._owm_hourly()
        except Exception:
            return []
        series: list[tuple[datetime, float]] = []
        for item in hourly:
            ts = item.get("dt")
            # One Call hourly has clouds as integer percent at top level
            clouds = item.get("clouds")
            if ts is None or clouds is None:
                continue
            t = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            if not (start <= t < end):
                continue
            # Clamp night-time production to 0 using a basic day window
            hour = t.hour
            pv = 0.0
            if 6 <= hour <= 20:
                pv = self._estimate_pv_from_clouds(float(clouds))
                # shape with a mild midday bump
                x = (hour - 13.0) / 7.0
                pv *= max(0.0, 1.0 - 0.3 * x * x)
            series.append((t, round(float(pv), 2)))
        # Ensure at least a point at start/end if holes
        return series

    def get_load_forecast(self, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        # No external source yet; use a shaped baseline similar to stub
        baseline = StubForecastProvider(base_load_watts=400.0)
        return baseline.get_load_forecast(start, end)


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
        # Include home ids to optionally select a specific home
        query = {
            "query": """
            query {
              viewer {
                 homes {
                   id
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
                ts_candidate = None
                try:
                    ts_candidate = datetime.fromisoformat(
                        starts_at.replace("Z", "+00:00")
                    ).astimezone(timezone.utc)
                except Exception:
                    ts_candidate = None
                if ts_candidate is None:
                    # Skip bad entries but keep processing other price points
                    continue
                series.append((ts_candidate, float(total)))
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
    peak_watts: float = 3500.0
    base_load_watts: float = 400.0

    def _hours(self, start: datetime, end: datetime) -> list[datetime]:
        start = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        end = end.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        hours = int(max(0, (end - start).total_seconds() // 3600))
        return [start + timedelta(hours=h) for h in range(hours + 1)]

    def get_solar_forecast(self, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        series: list[tuple[datetime, float]] = []
        for t in self._hours(start, end):
            # Simple bell curve between 06:00 and 20:00 UTC peaking at 13:00
            hour = t.hour + t.minute / 60.0
            if 6 <= hour <= 20:
                # Normalize to [0,1] with peak around 13
                x = (hour - 13.0) / 7.0
                val = max(0.0, 1.0 - x * x)  # inverted parabola
                watts = val * self.peak_watts
            else:
                watts = 0.0
            series.append((t, round(float(watts), 2)))
        return series

    def get_load_forecast(self, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        series: list[tuple[datetime, float]] = []
        for t in self._hours(start, end):
            hour = t.hour + t.minute / 60.0
            watts = self.base_load_watts
            # Morning bump 07–09
            if 7 <= hour <= 9:
                watts += 300
            # Midday modest usage
            if 12 <= hour <= 14:
                watts += 200
            # Evening peak 18–22
            if 18 <= hour <= 22:
                watts += 600
            series.append((t, float(watts)))
        return series


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
