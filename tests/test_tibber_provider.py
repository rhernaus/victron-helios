import json
from datetime import datetime, timezone, timedelta

import httpx
import pytest

from helios.providers import TibberPriceProvider


class DummyResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_tibber_provider_caches_between_calls(monkeypatch):
    # Build a fake today+tomorrow payload
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    today = [
        {"startsAt": (now + timedelta(hours=h)).isoformat().replace("+00:00", "Z"), "total": 0.10}
        for h in range(0, 24)
    ]
    tomorrow = [
        {"startsAt": (now + timedelta(hours=24 + h)).isoformat().replace("+00:00", "Z"), "total": 0.20}
        for h in range(0, 24)
    ]
    payload = {"data": {"viewer": {"homes": [{"currentSubscription": {"priceInfo": {"today": today, "tomorrow": tomorrow}}}]}}}

    calls = {"count": 0}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, json=None, headers=None):  # noqa: A002
            calls["count"] += 1
            return DummyResp(payload)

    monkeypatch.setattr(httpx, "Client", DummyClient)

    provider = TibberPriceProvider(access_token="dummy")
    start = now
    end = start + timedelta(hours=6)
    a = provider.get_prices(start, end)
    b = provider.get_prices(start, end)
    assert a and b
    # Should have made only one network call due to caching
    assert calls["count"] == 1
