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
