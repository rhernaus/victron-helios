from __future__ import annotations

from typing import Callable

from apscheduler.events import EVENT_JOB_MISSED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .metrics import scheduler_misfires_total
from .state import HeliosState


class HeliosScheduler:
    def __init__(self, state: HeliosState):
        self.state = state
        self.scheduler = BackgroundScheduler(timezone=state.settings.scheduler_timezone)

    def start(
        self,
        recalc_job: Callable[[], None],
        control_job: Callable[[], None],
        telemetry_job: Callable[[], None] | None = None,
    ) -> None:
        # Listen for misfires to expose as metrics
        self.scheduler.add_listener(lambda event: scheduler_misfires_total.inc(), EVENT_JOB_MISSED)
        self.scheduler.start()
        self._schedule_jobs(recalc_job, control_job, telemetry_job)

    def _schedule_jobs(
        self,
        recalc_job: Callable[[], None],
        control_job: Callable[[], None],
        telemetry_job: Callable[[], None] | None,
    ) -> None:
        settings = self.state.settings
        recalc_interval = settings.recalculation_interval_seconds
        # Jitter is at most 10% of interval, capped to 15s and always < interval
        recalc_jitter = min(
            max(0, recalc_interval // 10),  # ~10% jitter
            15,  # absolute cap to avoid large variance on long intervals
            max(0, recalc_interval - 1),  # ensure jitter < interval
        )
        self.scheduler.add_job(
            recalc_job,
            IntervalTrigger(
                seconds=recalc_interval,
                jitter=recalc_jitter,
            ),
            id="recalc",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=max(1, recalc_interval),
        )
        control_interval = settings.dbus_update_interval_seconds
        # Jitter is at most 10% of interval, capped to 2s and always < interval
        control_jitter = min(
            max(0, control_interval // 10),
            2,
            max(0, control_interval - 1),
        )
        self.scheduler.add_job(
            control_job,
            IntervalTrigger(
                seconds=control_interval,
                jitter=control_jitter,
            ),
            id="control",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=max(1, control_interval),
        )
        # Optional telemetry job
        if telemetry_job is not None:
            tel_interval = max(1, self.state.settings.telemetry_update_interval_seconds)
            tel_jitter = min(max(0, tel_interval // 10), 2, max(0, tel_interval - 1))
            self.scheduler.add_job(
                telemetry_job,
                IntervalTrigger(
                    seconds=tel_interval,
                    jitter=tel_jitter,
                ),
                id="telemetry",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=max(1, tel_interval),
            )

    def reschedule(
        self,
        recalc_job: Callable[[], None],
        control_job: Callable[[], None],
        telemetry_job: Callable[[], None] | None = None,
    ) -> None:
        self.scheduler.remove_all_jobs()
        self._schedule_jobs(recalc_job, control_job, telemetry_job)

    def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
