from datetime import datetime, timedelta, timezone

from helios.config import HeliosSettings
from helios.planner import Planner


def test_planner_respects_configurable_horizon():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    prices = [(now + timedelta(hours=h), 0.10 if h < 12 else 0.30) for h in range(0, 48)]
    settings = HeliosSettings(
        grid_import_limit_w=1000,
        planning_window_seconds=900,
        planning_horizon_hours=6,
    )
    planner = Planner(settings)
    plan = planner.build_plan(price_series=prices, now=now)
    assert len(plan.slots) == int(6 * 3600 / settings.planning_window_seconds)
