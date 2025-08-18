from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
import logging

from .models import Plan
from .dwell import DwellController

logger = logging.getLogger("helios")


class Executor(ABC):
    @abstractmethod
    def apply_setpoint(self, when: datetime, plan: Plan) -> None:
        """Apply the setpoint for the given time instant based on the plan."""


@dataclass
class NoOpExecutor(Executor):
    dwell: DwellController | None = None

    def apply_setpoint(self, when: datetime, plan: Plan) -> None:
        slot = plan.slot_for(when)
        if slot is None:
            return
        if self.dwell is not None and not self.dwell.should_change(slot.action, when):
            return
        if self.dwell is not None:
            self.dwell.note_action(slot.action, when)
        logger.info(
            "NoOpExecutor applying setpoint W=%s action=%s at=%s",
            slot.target_grid_setpoint_w,
            slot.action.value,
            when.isoformat(),
        )

