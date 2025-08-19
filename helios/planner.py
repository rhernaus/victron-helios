from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Optional

from .config import HeliosSettings
from .models import Action, Plan, PlanSlot


class Planner:
    def __init__(self, settings: HeliosSettings):
        self.settings = settings

    def build_plan(
        self,
        price_series: list[tuple[datetime, float]],
        now: Optional[datetime] = None,
    ) -> Plan:
        now = now or datetime.now(timezone.utc)
        window = self.settings.planning_window_seconds
        horizon_hours = self.settings.planning_horizon_hours
        generated_at = now

        # Build time slices
        slots: list[PlanSlot] = []
        start = now
        end = now + timedelta(hours=horizon_hours)

        # Compute a simple price threshold using median
        prices = [p for _, p in price_series]
        pivot = median(prices) if prices else 0.0

        t = start
        while t < end:
            slice_end = min(t + timedelta(seconds=window), end)
            # approximate price at slice midpoint
            midpoint = t + (slice_end - t) / 2
            price_mid = self._price_at(price_series, midpoint) or pivot

            action, setpoint = self._decide_action(price_mid, pivot)

            # Provide a brief reason per slot mirroring the decision at midpoint
            reason = self._reason_for(action, setpoint, price_mid, pivot)
            slot = PlanSlot(
                start=t,
                end=slice_end,
                action=action,
                target_grid_setpoint_w=setpoint,
                reason=reason,
            )
            # Derive simple energy flow estimates from setpoint and price for visualizations
            self._annotate_energy_and_costs(slot, price_mid)
            slots.append(slot)
            t = slice_end

        # Build a brief plan summary
        num_charge = sum(1 for s in slots if s.action == Action.CHARGE_FROM_GRID)
        num_export = sum(1 for s in slots if s.action == Action.EXPORT_TO_GRID)
        num_idle = sum(1 for s in slots if s.action == Action.IDLE)
        summary = (
            f"H{horizon_hours}h: charge={num_charge}, export={num_export}, "
            f"idle={num_idle}; pivot={pivot:.3f}"
        )
        return Plan(
            generated_at=generated_at,
            planning_window_seconds=window,
            slots=slots,
            summary=summary,
        )

    def _annotate_energy_and_costs(self, slot: PlanSlot, raw_price_eur_per_kwh: float) -> None:
        """Populate PlanSlot with rough energy flow and cost estimates.

        This is not a physical model; it apportions energy solely based on grid
        setpoint and assumes no curtailment. It is intended for UI graphs only.
        """
        secs = int((slot.end - slot.start).total_seconds())
        kwh = abs(slot.target_grid_setpoint_w) * (secs / 3600.0) / 1000.0

        # Prices adjusted per settings (buy and sell differ)
        buy = (
            raw_price_eur_per_kwh * self.settings.buy_price_multiplier
            + self.settings.buy_price_fixed_fee_eur_per_kwh
        )
        sell = (
            raw_price_eur_per_kwh * self.settings.sell_price_multiplier
            - self.settings.sell_price_fixed_deduction_eur_per_kwh
        )

        # Battery round-trip loss and degradation cost
        eff = max(0.0, min(100.0, self.settings.battery_roundtrip_efficiency_percent)) / 100.0
        cycle_cost = max(0.0, self.settings.battery_cycle_cost_eur_per_kwh)

        slot.solar_to_grid_kwh = 0.0
        slot.solar_to_battery_kwh = 0.0
        slot.solar_to_usage_kwh = 0.0
        slot.battery_to_grid_kwh = 0.0
        slot.battery_to_usage_kwh = 0.0
        slot.grid_to_usage_kwh = 0.0
        slot.grid_to_battery_kwh = 0.0

        slot.grid_cost_eur = 0.0
        slot.grid_savings_eur = 0.0
        slot.battery_cost_eur = 0.0

        if slot.target_grid_setpoint_w > 0:
            # Import from grid; assume it charges the battery when action is charge
            if slot.action == Action.CHARGE_FROM_GRID:
                slot.grid_to_battery_kwh = kwh
                # battery throughput cost and roundtrip loss priced at buy
                throughput = kwh
                loss_kwh = kwh * (1 - eff)
                slot.battery_cost_eur = throughput * cycle_cost + loss_kwh * buy
            else:
                slot.grid_to_usage_kwh = kwh
            slot.grid_cost_eur = kwh * buy
        elif slot.target_grid_setpoint_w < 0:
            # Export to grid; assume energy originates from battery
            slot.battery_to_grid_kwh = kwh
            throughput = kwh / max(1e-6, eff)
            degradation = throughput * cycle_cost
            slot.battery_cost_eur = degradation
            slot.grid_savings_eur = kwh * sell
        else:
            # Idle: no grid cost/savings; not modeling solar/load here
            pass

    def _decide_action(self, price_mid: float, pivot: float) -> tuple[Action, int]:
        # Simple heuristic:
        # - If grid sell enabled and price is high => export at max allowed; else idle
        # - If price is low => import/charge at max allowed
        # In all cases obey configured grid limits and use settings-defined
        # limits rather than hard-coded values. Apply buy/sell multipliers and
        # hysteresis around pivot to reduce flapping.
        import_limit = self.settings.grid_import_limit_w or 0
        export_limit = self.settings.grid_export_limit_w or 0
        # Respect battery power limits if provided (planner-level clamp)
        battery_charge_limit = self.settings.battery_charge_limit_w or import_limit
        battery_discharge_limit = self.settings.battery_discharge_limit_w or export_limit
        # Optional SoC policy: block charge above max SoC, block export below reserve
        soc = self.settings.assumed_current_soc_percent

        # Default idle
        action = Action.IDLE
        setpoint = 0

        # Apply simple price adjustments for decision thresholding
        buy_price = (
            price_mid * self.settings.buy_price_multiplier
            + self.settings.buy_price_fixed_fee_eur_per_kwh
        )
        sell_price = (
            price_mid * self.settings.sell_price_multiplier
            - self.settings.sell_price_fixed_deduction_eur_per_kwh
        )

        hysteresis = self.settings.price_hysteresis_eur_per_kwh
        cheap = buy_price <= (pivot - hysteresis)
        expensive = sell_price >= (pivot + hysteresis)

        if cheap and import_limit > 0:
            action = Action.CHARGE_FROM_GRID
            # Use configured limit; planner may later incorporate battery
            # charge limit and pricing formulas
            # If SoC provided and already at/above max, do not charge
            if soc is not None and soc >= self.settings.max_soc_percent:
                action = Action.IDLE
                setpoint = 0
            else:
                setpoint = min(import_limit, battery_charge_limit)
                # Keep a compact explanatory string under lint limits (not used here)
        elif self.settings.grid_sell_enabled and expensive and export_limit > 0:
            action = Action.EXPORT_TO_GRID
            # Negative setpoint for export; clamp by grid and battery discharge limit
            # If SoC provided and at/below reserve, do not export
            if soc is not None and soc <= self.settings.reserve_soc_percent:
                action = Action.IDLE
                setpoint = 0
            else:
                setpoint = -min(export_limit, battery_discharge_limit)
        else:
            # No need to keep reason here; caller derives a message
            pass

        return action, setpoint

    def _reason_for(self, action: Action, setpoint: int, price_mid: float, pivot: float) -> str:
        # Synthesize a compact explanation based on the same logic
        hysteresis = self.settings.price_hysteresis_eur_per_kwh
        buy_price = (
            price_mid * self.settings.buy_price_multiplier
            + self.settings.buy_price_fixed_fee_eur_per_kwh
        )
        sell_price = (
            price_mid * self.settings.sell_price_multiplier
            - self.settings.sell_price_fixed_deduction_eur_per_kwh
        )
        if action == Action.CHARGE_FROM_GRID:
            return (
                f"cheap {buy_price:.3f} <= pivot-hyst {(pivot - hysteresis):.3f}; "
                f"setpoint {setpoint}W"
            )
        if action == Action.EXPORT_TO_GRID:
            return (
                f"expensive {sell_price:.3f} >= pivot+hyst {(pivot + hysteresis):.3f}; "
                f"setpoint {setpoint}W"
            )
        return "idle: price within hysteresis or constrained"

    @staticmethod
    def _price_at(series: list[tuple[datetime, float]], at: datetime) -> Optional[float]:
        if not series:
            return None
        # series assumed hourly; pick closest
        closest = min(series, key=lambda p: abs((p[0] - at).total_seconds()))
        return closest[1]
