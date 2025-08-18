from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .config import HeliosSettings
from .models import Plan


@dataclass
class HeliosState:
    settings: HeliosSettings
    scheduler: Optional[object] = None
    planner: Optional[object] = None
    executor: Optional[object] = None

    latest_plan: Optional[Plan] = None
    last_recalc_at: Optional[datetime] = None
    last_control_at: Optional[datetime] = None
    automation_paused: bool = False


_global_state: Optional[HeliosState] = None


def get_state() -> HeliosState:
    global _global_state
    if _global_state is None:
        _global_state = HeliosState(settings=HeliosSettings())
    return _global_state
