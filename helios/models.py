from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Action(str, Enum):
    CHARGE_FROM_GRID = "charge_from_grid"
    DISCHARGE_TO_LOAD = "discharge_to_load"
    EXPORT_TO_GRID = "export_to_grid"
    IDLE = "idle"


class PlanSlot(BaseModel):
    start: datetime
    end: datetime
    action: Action
    target_grid_setpoint_w: int = Field(
        description=(
            "Positive means import from grid in Watts. Negative means export to grid in Watts."
        )
    )


class Plan(BaseModel):
    generated_at: datetime
    planning_window_seconds: int
    slots: list[PlanSlot] = Field(default_factory=list)

    def slot_for(self, at: datetime) -> Optional[PlanSlot]:
        for slot in self.slots:
            if slot.start <= at < slot.end:
                return slot
        return None
