from __future__ import annotations

from datetime import datetime
from enum import Enum

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
    reason: str | None = Field(default=None, description="Brief rationale for this action")
    # Optional energy flow estimates (kWh for the slot). Positive numbers.
    solar_to_grid_kwh: float | None = None
    solar_to_battery_kwh: float | None = None
    solar_to_usage_kwh: float | None = None
    battery_to_grid_kwh: float | None = None
    battery_to_usage_kwh: float | None = None
    grid_to_usage_kwh: float | None = None
    grid_to_battery_kwh: float | None = None
    # Costs for the slot
    grid_cost_eur: float | None = None
    grid_savings_eur: float | None = None
    battery_cost_eur: float | None = None


class Plan(BaseModel):
    generated_at: datetime
    planning_window_seconds: int
    slots: list[PlanSlot] = Field(default_factory=list)
    summary: str | None = Field(default=None, description="High-level plan summary")

    def slot_for(self, at: datetime) -> PlanSlot | None:
        for slot in self.slots:
            if slot.start <= at < slot.end:
                return slot
        return None


class StatusResponse(BaseModel):
    automation_paused: bool
    last_recalc_at: datetime | None
    last_control_at: datetime | None
    current_action: Action | None = None
    current_setpoint_w: int | None = None
    current_reason: str | None = None
    # Telemetry snapshot
    soc_percent: float | None = None
    load_w: int | None = None
    solar_w: int | None = None
    ev_charger_status: dict | None = None


class ConfigResponse(BaseModel):
    data: dict
