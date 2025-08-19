from __future__ import annotations

from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_MISSED

from .state import HeliosState
from .metrics import scheduler_misfires_total


class HeliosScheduler:
    def __init__(self, state: HeliosState):
        self.state = state
        self.scheduler = BackgroundScheduler(timezone=state.settings.scheduler_timezone)

    def start(self, recalc_job: Callable[[], None], control_job: Callable[[], None]) -> None:
        # Listen for misfires to expose as metrics
        self.scheduler.add_listener(lambda event: scheduler_misfires_total.inc(), EVENT_JOB_MISSED)
        self.scheduler.start()
        self._schedule_jobs(recalc_job, control_job)

    def _schedule_jobs(
        self,
        recalc_job: Callable[[], None],
        control_job: Callable[[], None],
    ) -> None:
        settings = self.state.settings
        self.scheduler.add_job(
            recalc_job,
            IntervalTrigger(
                seconds=settings.recalculation_interval_seconds,
                # Allow up to 10% jitter (at least 1s) to avoid thundering herd
                jitter=max(1, settings.recalculation_interval_seconds // 10),
            ),
            id="recalc",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=max(1, settings.recalculation_interval_seconds),
        )
        self.scheduler.add_job(
            control_job,
            IntervalTrigger(
                seconds=settings.dbus_update_interval_seconds,
                jitter=max(1, settings.dbus_update_interval_seconds // 10),
            ),
            id="control",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=max(1, settings.dbus_update_interval_seconds),
        )

    def reschedule(self, recalc_job: Callable[[], None], control_job: Callable[[], None]) -> None:
        self.scheduler.remove_all_jobs()
        self._schedule_jobs(recalc_job, control_job)

    def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
