"""The worker process runner. Built-ins are used as work functions because they pickle cleanly."""

import os
import time

import pytest

from meeting_indexer.limits import TimeLimitExceeded, WorkerDied, run_with_time_limit


def test_result_is_returned() -> None:
    assert run_with_time_limit(pow, 2, 10, seconds=30) == 1024


def test_slow_work_is_stopped_at_the_limit() -> None:
    started = time.monotonic()

    with pytest.raises(TimeLimitExceeded, match="1 s time limit"):
        run_with_time_limit(time.sleep, 60, seconds=1)

    assert time.monotonic() - started < 10


def test_exception_in_the_worker_is_raised_with_its_traceback() -> None:
    with pytest.raises(ValueError, match="invalid literal") as error:
        run_with_time_limit(int, "ei numero", seconds=30)
    assert "Traceback" in error.value.worker_traceback  # pyright: ignore[reportAttributeAccessIssue]


def test_crashed_worker_is_reported() -> None:
    with pytest.raises(WorkerDied, match="exit code 3"):
        run_with_time_limit(os._exit, 3, seconds=30)
