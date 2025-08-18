from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
import logging

from .dwell import DwellController
from .metrics import executor_apply_failures_total, executor_apply_seconds
from .models import Plan

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
        with executor_apply_seconds.time():
            logger.info(
                "NoOpExecutor applying setpoint W=%s action=%s at=%s",
                slot.target_grid_setpoint_w,
                slot.action.value,
                when.isoformat(),
            )


@dataclass
class DbusExecutor(Executor):
    dwell: DwellController | None = None

    def apply_setpoint(self, when: datetime, plan: Plan) -> None:
        slot = plan.slot_for(when)
        if slot is None:
            return
        if self.dwell is not None and not self.dwell.should_change(slot.action, when):
            return
        if self.dwell is not None:
            self.dwell.note_action(slot.action, when)
        # Placeholder for future D-Bus integration
        try:
            with executor_apply_seconds.time():
                logger.info(
                    "DbusExecutor would set grid setpoint W=%s action=%s at=%s",
                    slot.target_grid_setpoint_w,
                    slot.action.value,
                    when.isoformat(),
                )
        except Exception:
            executor_apply_failures_total.inc()
            raise
