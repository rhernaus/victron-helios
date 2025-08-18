from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import TYPE_CHECKING, Optional

from .config import HeliosSettings
from .dwell import DwellController
from .executor import Executor
from .models import Plan
from .providers import PriceProvider

if TYPE_CHECKING:
    from .planner import Planner
    from .scheduler import HeliosScheduler


@dataclass
class HeliosState:
    settings: HeliosSettings
    scheduler: Optional[HeliosScheduler] = None
    planner: Optional[Planner] = None
    executor: Optional[Executor] = None
    price_provider: Optional[PriceProvider] = None

    latest_plan: Optional[Plan] = None
    last_recalc_at: Optional[datetime] = None
    last_control_at: Optional[datetime] = None
    automation_paused: bool = False
    lock: RLock = field(default_factory=RLock)
    dwell: DwellController = field(default_factory=lambda: DwellController(minimum_dwell_seconds=0))


_global_state: Optional[HeliosState] = None


def get_state() -> HeliosState:
    global _global_state
    if _global_state is None:
        _global_state = HeliosState(settings=HeliosSettings())
    return _global_state


def _reset_state_for_testing() -> None:
    """Reset global state singleton. For test usage only."""
    global _global_state
    _global_state = None
