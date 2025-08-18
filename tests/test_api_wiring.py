import time

from fastapi.testclient import TestClient

from helios.api import create_app
from helios.config import ConfigUpdate, HeliosSettings
from helios.state import _reset_state_for_testing


def test_provider_selection_stub_and_horizon_config():
    _reset_state_for_testing()
    settings = HeliosSettings(
        price_provider="stub",
        planning_window_seconds=600,
        planning_horizon_hours=2,
        grid_import_limit_w=1000,
    )
    app = create_app(initial_settings=settings)
    with TestClient(app) as client:
        # First call triggers planning on startup; validate horizon-derived slot count
        resp = client.get("/plan")
        if resp.status_code != 200:
            # brief wait and retry in case startup is not fully completed yet
            for _ in range(20):
                time.sleep(0.05)
                resp = client.get("/plan")
                if resp.status_code == 200:
                    break
        assert resp.status_code == 200
        data = resp.json()
    assert data["planning_window_seconds"] == 600
    # Expect 2 hours / 10-minute windows = 12 slots
    assert len(data["slots"]) == int(2 * 3600 / 600)


def test_config_put_updates_dwell_and_executor_backend():
    _reset_state_for_testing()
    settings = HeliosSettings(minimum_action_dwell_seconds=0, executor_backend="noop")
    app = create_app(initial_settings=settings)
    with TestClient(app) as client:
        # Update dwell and backend
        update = ConfigUpdate(minimum_action_dwell_seconds=300, executor_backend="dbus")
        resp = client.put("/config", json=update.model_dump())
        assert resp.status_code == 200

        # Confirm it sticks via GET /config
        cfg = client.get("/config").json()["data"]
        assert cfg["minimum_action_dwell_seconds"] == 300
        assert cfg["executor_backend"] == "dbus"


def test_tibber_provider_cache_persists_across_recalc(monkeypatch):
    _reset_state_for_testing()
    # Prepare a fake Tibber response and ensure only one HTTP call happens across two recalc windows
    import httpx

    from helios.api import create_app

    calls = {"count": 0}

    class DummyResp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, json=None, headers=None):  # noqa: A002
            calls["count"] += 1
            # minimal valid payload for two days
            from datetime import datetime, timedelta, timezone

            now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
            today = [
                {
                    "startsAt": (now + timedelta(hours=h)).isoformat().replace("+00:00", "Z"),
                    "total": 0.10,
                }
                for h in range(0, 24)
            ]
            tomorrow = [
                {
                    "startsAt": (now + timedelta(hours=24 + h)).isoformat().replace("+00:00", "Z"),
                    "total": 0.20,
                }
                for h in range(0, 24)
            ]
            payload = {
                "data": {
                    "viewer": {
                        "homes": [
                            {
                                "currentSubscription": {
                                    "priceInfo": {"today": today, "tomorrow": tomorrow}
                                }
                            }
                        ]
                    }
                }
            }
            return DummyResp(payload)

    monkeypatch.setattr(httpx, "Client", DummyClient)

    settings = HeliosSettings(
        price_provider="tibber",
        tibber_token="token",
        planning_window_seconds=900,
    )
    app = create_app(initial_settings=settings)
    with TestClient(app) as client:
        # First plan generation
        for _ in range(20):
            r = client.get("/plan")
            if r.status_code == 200:
                break
            time.sleep(0.05)
        assert r.status_code == 200
        # Force another recalc by hitting status and waiting briefly
        client.get("/status")
        for _ in range(20):
            r = client.get("/plan")
            if r.status_code == 200:
                break
            time.sleep(0.05)
        assert r.status_code == 200

    # Only one upstream call due to provider reuse + internal cache
    assert calls["count"] == 1
