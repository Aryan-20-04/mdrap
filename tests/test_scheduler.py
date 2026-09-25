"""
Unit tests for Scheduled Reports & Task Scheduler (Gap 12).
"""

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import time
import datetime
import pytest
from scheduler import Scheduler, ScheduledJob, CronParser


def test_cron_parser_hourly():
    now = time.time()
    next_ts = CronParser.next_run_after("@hourly", now)
    assert next_ts > now
    dt = datetime.datetime.fromtimestamp(next_ts)
    assert dt.minute == 0


def test_cron_parser_daily():
    now = time.time()
    next_ts = CronParser.next_run_after("@daily", now)
    assert next_ts > now
    dt = datetime.datetime.fromtimestamp(next_ts)
    assert dt.minute == 0
    assert dt.hour == 0


def test_cron_parser_custom_interval():
    # Every 15 minutes: */15 * * * *
    now = time.time()
    next_ts = CronParser.next_run_after("*/15 * * * *", now)
    assert next_ts > now
    dt = datetime.datetime.fromtimestamp(next_ts)
    assert dt.minute in {0, 15, 30, 45}


def test_scheduler_add_and_list_jobs():
    s = Scheduler()
    flag = False

    def sample_action():
        nonlocal flag
        flag = True
        return "success"

    job = s.add_job("TestJob", "@hourly", sample_action, job_id="job1")
    assert job.job_id == "job1"
    assert job.name == "TestJob"
    assert job.enabled is True

    jobs = s.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].job_id == "job1"


def test_scheduler_remove_and_enable():
    s = Scheduler()
    job = s.add_job("Job1", "@hourly", lambda: None, job_id="j1")
    assert s.get_job("j1") is not None

    s.enable_job("j1", False)
    assert s.get_job("j1").enabled is False

    removed = s.remove_job("j1")
    assert removed is True
    assert s.get_job("j1") is None


def test_scheduler_check_and_run():
    s = Scheduler()
    counter = 0

    def inc():
        nonlocal counter
        counter += 1
        return counter

    job = s.add_job("CounterJob", "* * * * *", inc, job_id="c1")

    # Simulate a future time where next_run is reached
    future_time = job.next_run + 5.0
    executed = s.check_and_run(current_time=future_time)

    assert len(executed) == 1
    assert executed[0].job_id == "c1"
    assert counter == 1
    assert job.run_count == 1
    assert job.last_result == 1
    assert job.next_run > future_time


def test_scheduler_error_handling():
    s = Scheduler()

    def buggy():
        raise RuntimeError("Something failed")

    job = s.add_job("BuggyJob", "* * * * *", buggy, job_id="bug1")
    future_time = job.next_run + 1.0
    executed = s.check_and_run(current_time=future_time)

    assert len(executed) == 1
    assert "Something failed" in job.last_error
    assert job.last_result is None


def test_generate_eod_report():
    s = Scheduler()
    report = s.generate_eod_report()
    assert "timestamp" in report
    assert "portfolio" in report
    assert "market_data" in report
