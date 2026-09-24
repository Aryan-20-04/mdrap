"""
Scheduled Reports & Data Snapshots Engine for MDRAP.

This module provides a lightweight cron-like scheduler for recurring tasks:
EOD portfolio snapshots, bar aggregations, report generation, and system health checks.
"""

import time
import datetime
import itertools
from dataclasses import dataclass
from typing import Callable, Any

__stability__ = "beta"


@dataclass
class ScheduledJob:
    job_id: str
    name: str
    cron_expr: (
        str  # e.g. '0 16 * * 1-5' (minute hour dom month dow) or '@daily', '@hourly'
    )
    action_fn: Callable[[], Any]
    last_run: float = 0.0
    next_run: float = 0.0
    run_count: int = 0
    enabled: bool = True
    last_result: Any = None
    last_error: str = ""


class CronParser:
    ALIASES = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@eod": "0 16 * * 1-5"}

    @staticmethod
    def parse_field(field_str: str, min_val: int, max_val: int) -> set[int]:
        if field_str == "*":
            return set(range(min_val, max_val + 1))

        values = set()
        parts = field_str.split(",")
        for part in parts:
            if "/" in part:
                range_str, step_str = part.split("/")
                step = int(step_str)
                if range_str == "*":
                    start, end = min_val, max_val
                else:
                    start_str, end_str = range_str.split("-")
                    start, end = int(start_str), int(end_str)
                values.update(range(start, end + 1, step))
            elif "-" in part:
                start_str, end_str = part.split("-")
                values.update(range(int(start_str), int(end_str) + 1))
            else:
                values.add(int(part))
        return values

    @classmethod
    def next_run_after(cls, cron_expr: str, after_timestamp: float) -> float:
        """Calculates the next run time after the given timestamp."""
        expr = cls.ALIASES.get(cron_expr.lower(), cron_expr)
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: {cron_expr}")

        minutes = cls.parse_field(parts[0], 0, 59)
        hours = cls.parse_field(parts[1], 0, 23)
        days = cls.parse_field(parts[2], 1, 31)
        months = cls.parse_field(parts[3], 1, 12)
        dows = cls.parse_field(
            parts[4], 0, 6
        )  # 0 is Sunday, 1 is Monday ... 6 is Saturday

        # Adjust 7 to 0 if present (Sunday)
        if 7 in dows:
            dows.remove(7)
            dows.add(0)

        # Treat * * * * * as allowing any DOW and any DOM
        # If DOW is specified but DOM is *, or vice versa, the logic can be complex
        # We will keep it simple and require both to match if specified

        dt = datetime.datetime.fromtimestamp(after_timestamp)
        # Start looking from the next minute
        dt = dt.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)

        # Cap the search to 5 years
        for _ in range(5 * 365 * 24 * 60):
            if (
                dt.month in months
                and dt.day in days
                and dt.hour in hours
                and dt.minute in minutes
                and dt.isoweekday() % 7 in dows
            ):  # isoweekday: Mon=1...Sun=7 -> Mon=1...Sun=0
                return dt.timestamp()
            dt += datetime.timedelta(minutes=1)

        raise ValueError("Could not find next run time for cron expression")


class Scheduler:
    def __init__(self):
        self._jobs: dict[str, ScheduledJob] = {}
        self._running = False
        self._id_counter = itertools.count(1)

    def add_job(
        self,
        name: str,
        cron_expr: str,
        action: Callable[[], Any],
        job_id: str | None = None,
    ) -> ScheduledJob:
        if job_id is None:
            job_id = f"job_{next(self._id_counter)}"

        job = ScheduledJob(
            job_id=job_id, name=name, cron_expr=cron_expr, action_fn=action
        )

        current_time = time.time()
        job.next_run = CronParser.next_run_after(job.cron_expr, current_time)
        self._jobs[job_id] = job
        return job

    def remove_job(self, job_id: str) -> bool:
        if job_id in self._jobs:
            del self._jobs[job_id]
            return True
        return False

    def enable_job(self, job_id: str, enabled: bool = True):
        if job_id in self._jobs:
            self._jobs[job_id].enabled = enabled

    def get_job(self, job_id: str) -> ScheduledJob | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[ScheduledJob]:
        return list(self._jobs.values())

    def check_and_run(self, current_time: float | None = None) -> list[ScheduledJob]:
        """Checks all enabled jobs whose next_run <= current_time and executes them synchronously."""
        if current_time is None:
            current_time = time.time()

        executed_jobs = []
        for job in self._jobs.values():
            if job.enabled and job.next_run <= current_time:
                job.last_run = current_time
                try:
                    result = job.action_fn()
                    job.last_result = result
                    job.last_error = ""
                except Exception as e:
                    job.last_error = str(e)
                    job.last_result = None

                job.run_count += 1
                job.next_run = CronParser.next_run_after(job.cron_expr, current_time)
                executed_jobs.append(job)

        return executed_jobs

    def generate_eod_report(
        self, portfolio_tracker: Any = None, bar_db: Any = None
    ) -> dict:
        """Generates EOD summary dict with portfolio equity, pnl, active positions, bar counts."""
        report = {
            "timestamp": time.time(),
            "datetime": datetime.datetime.now().isoformat(),
            "portfolio": {},
            "market_data": {},
        }

        if portfolio_tracker:
            report["portfolio"] = {
                "equity": getattr(portfolio_tracker, "equity", 0.0),
                "pnl": getattr(portfolio_tracker, "pnl", 0.0),
                "active_positions": len(getattr(portfolio_tracker, "positions", [])),
            }

        if bar_db:
            try:
                # Example metric, assumes bar_db has a method to get total counts or similar
                report["market_data"]["total_bars"] = getattr(
                    bar_db, "total_bar_count", 0
                )
            except Exception:
                pass

        return report

    def schedule_retention(
        self,
        store: Any,
        cron_expr: str = "@daily",
        retain_days: int = 30,
        quarantine_days: int = 90,
    ) -> ScheduledJob:
        """Schedules recurring data retention pruning and WAL checkpoint compaction."""
        return self.add_job(
            name="data_retention_compact",
            cron_expr=cron_expr,
            action=lambda: store.retention_compact(
                retain_days=retain_days, quarantine_days=quarantine_days
            ),
        )
