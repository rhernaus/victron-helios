from datetime import datetime, timedelta, timezone

from helios.config import HeliosSettings
from helios.models import Action
from helios.planner import Planner


def test_decide_action_import_when_cheap():
    settings = HeliosSettings(grid_import_limit_w=1500, grid_export_limit_w=2000)
    planner = Planner(settings)
    action, setpoint = planner._decide_action(price_mid=0.10, pivot=0.20)
    assert action == Action.CHARGE_FROM_GRID
    assert setpoint == 1500


def test_decide_action_export_when_expensive_and_sell_enabled():
    settings = HeliosSettings(
        grid_import_limit_w=1500,
        grid_export_limit_w=2000,
        grid_sell_enabled=True,
    )
    planner = Planner(settings)
    action, setpoint = planner._decide_action(price_mid=0.30, pivot=0.20)
    assert action == Action.EXPORT_TO_GRID
    assert setpoint == -2000


def test_build_plan_uses_time_slices_and_returns_slots():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    prices = [(now + timedelta(hours=h), 0.10 if h < 12 else 0.30) for h in range(0, 24)]
    settings = HeliosSettings(grid_import_limit_w=1000)
    planner = Planner(settings)
    plan = planner.build_plan(price_series=prices, now=now)
    assert plan.planning_window_seconds == settings.planning_window_seconds
    assert len(plan.slots) == int(24 * 3600 / settings.planning_window_seconds)
    # When window divides an hour, slots align to the hour boundary
    assert plan.slots[0].start == now.replace(minute=0, second=0, microsecond=0)
