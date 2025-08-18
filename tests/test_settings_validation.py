import pytest

from helios.config import HeliosSettings, ConfigUpdate


def test_recalc_interval_must_be_leq_planning_window():
    with pytest.raises(ValueError):
        HeliosSettings(planning_window_seconds=300, recalculation_interval_seconds=600)


def test_soc_bounds_and_reserve_validation():
    with pytest.raises(ValueError):
        HeliosSettings(min_soc_percent=50, max_soc_percent=40)
    with pytest.raises(ValueError):
        HeliosSettings(min_soc_percent=10, reserve_soc_percent=5, max_soc_percent=90)
    # valid
    s = HeliosSettings(min_soc_percent=10, reserve_soc_percent=40, max_soc_percent=90)
    assert s.min_soc_percent == 10


def test_config_update_applies_atomically_and_validates():
    settings = HeliosSettings(planning_window_seconds=900, recalculation_interval_seconds=300)
    # Valid update
    update = ConfigUpdate(recalculation_interval_seconds=600)
    new_settings = update.apply_to(settings)
    assert new_settings.recalculation_interval_seconds == 600
    # Invalid update (violates invariant)
    bad = ConfigUpdate(recalculation_interval_seconds=1800)
    with pytest.raises(ValueError):
        bad.apply_to(settings)
