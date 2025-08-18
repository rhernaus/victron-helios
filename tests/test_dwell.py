from datetime import datetime, timedelta, timezone

from helios.dwell import DwellController
from helios.models import Action


def test_dwell_prevents_rapid_flapping():
    now = datetime.now(timezone.utc)
    dwell = DwellController(minimum_dwell_seconds=300)

    # First action permitted
    assert dwell.should_change(Action.CHARGE_FROM_GRID, now)
    dwell.note_action(Action.CHARGE_FROM_GRID, now)

    # Immediate change not permitted
    assert not dwell.should_change(Action.EXPORT_TO_GRID, now + timedelta(seconds=60))

    # After dwell window, permitted
    later = now + timedelta(seconds=301)
    assert dwell.should_change(Action.EXPORT_TO_GRID, later)
    dwell.note_action(Action.EXPORT_TO_GRID, later)
