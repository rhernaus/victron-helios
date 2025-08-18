from datetime import datetime, timedelta, timezone

from helios.providers import StubPriceProvider


def test_stub_price_provider_generates_series():
    provider = StubPriceProvider(swing_low=0.10, swing_high=0.30)
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=6)
    series = provider.get_prices(start, end)
    assert len(series) >= 6
    assert all(isinstance(p[0], datetime) and isinstance(p[1], float) for p in series)
