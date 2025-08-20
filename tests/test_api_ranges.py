import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from helios.api import create_app
from helios.config import HeliosSettings
from helios.state import _reset_state_for_testing


def _await_plan(client: TestClient, attempts: int = 40) -> None:
    for _ in range(attempts):
        r = client.get("/plan")
        if r.status_code == 200:
            return
        time.sleep(0.05)
    raise AssertionError("plan not ready in test")


def test_prices_endpoint_supports_from_to_range():
    _reset_state_for_testing()
    settings = HeliosSettings(price_provider="stub", planning_horizon_hours=6)
    app = create_app(initial_settings=settings)
    with TestClient(app) as client:
        _await_plan(client)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        frm = int((now - timedelta(hours=1)).timestamp())
        to = int((now + timedelta(hours=1)).timestamp())
        resp = client.get(f"/prices?from={frm}&to={to}")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data and isinstance(data["items"], list)
        assert len(data["items"]) >= 1
        first = data["items"][0]
        # ensure keys are present as expected
        assert set(["t", "raw", "buy", "sell"]).issubset(first.keys())


def test_telemetry_history_supports_from_to_range():
    _reset_state_for_testing()
    settings = HeliosSettings()
    app = create_app(initial_settings=settings)
    with TestClient(app) as client:
        # prime telemetry on startup
        client.get("/status")
        # fetch recent history to derive a concrete timestamp window to query
        recent = client.get("/telemetry/history?limit=5").json().get("items", [])
        if recent:
            ts = int(datetime.fromisoformat(recent[-1]["t"]).timestamp())
            resp = client.get(f"/telemetry/history?from={ts}&to={ts}")
            assert resp.status_code == 200
            items = resp.json().get("items", [])
            # zero or one row is acceptable, but endpoint must be functional
            assert isinstance(items, list)
        else:
            # If no sqlite available, still ensure endpoint responds
            resp = client.get("/telemetry/history?from=0&to=0")
            assert resp.status_code == 200
