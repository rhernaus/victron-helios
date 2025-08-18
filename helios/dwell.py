from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .models import Action


@dataclass
class DwellController:
    minimum_dwell_seconds: int = 0
    last_action: Optional[Action] = None
    last_action_at: Optional[datetime] = None

    def should_change(self, new_action: Action, now: datetime) -> bool:
        if self.last_action is None or self.last_action == new_action:
            return True
        if self.minimum_dwell_seconds <= 0:
            return True
        if self.last_action_at is None:
            return True
        elapsed = (now - self.last_action_at).total_seconds()
        return elapsed >= self.minimum_dwell_seconds

    def note_action(self, action: Action, now: datetime) -> None:
        if self.last_action != action:
            self.last_action = action
            self.last_action_at = now

