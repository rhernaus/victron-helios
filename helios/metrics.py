from __future__ import annotations

from prometheus_client import Counter, Gauge, Summary

planner_runs_total = Counter("helios_planner_runs_total", "Number of planner recalculations")
control_ticks_total = Counter("helios_control_ticks_total", "Number of control loop ticks")
current_setpoint_watts = Gauge("helios_current_setpoint_watts", "Current grid setpoint in Watts")
automation_paused = Gauge(
    "helios_automation_paused", "Automation paused state (1 paused, 0 running)"
)

# Provider metrics
price_provider_requests_total = Counter(
    "helios_price_provider_requests_total",
    "Price provider request count by provider and result",
    labelnames=("provider", "result"),
)
price_provider_request_seconds = Summary(
    "helios_price_provider_request_seconds",
    "Duration of price provider requests in seconds",
    labelnames=("provider",),
)
price_provider_failures_total = Counter(
    "helios_price_provider_failures_total", "Total failures fetching prices"
)

# Plan metrics
plan_age_seconds = Gauge("helios_plan_age_seconds", "Age of the latest plan in seconds")

# Executor metrics
executor_apply_seconds = Summary(
    "helios_executor_apply_seconds", "Duration of executor apply operations in seconds"
)
executor_apply_failures_total = Counter(
    "helios_executor_apply_failures_total", "Executor apply failures"
)

# Scheduler metrics
recalc_job_runs_total = Counter("helios_recalc_job_runs_total", "Number of recalc job executions")
control_job_runs_total = Counter(
    "helios_control_job_runs_total", "Number of control job executions"
)
scheduler_misfires_total = Counter(
    "helios_scheduler_misfires_total", "Number of scheduler job misfires"
)
