from __future__ import annotations

from prometheus_client import Counter, Gauge

planner_runs_total = Counter("helios_planner_runs_total", "Number of planner recalculations")
control_ticks_total = Counter("helios_control_ticks_total", "Number of control loop ticks")
current_setpoint_watts = Gauge("helios_current_setpoint_watts", "Current grid setpoint in Watts")
automation_paused = Gauge(
    "helios_automation_paused", "Automation paused state (1 paused, 0 running)"
)
