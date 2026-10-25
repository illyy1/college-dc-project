"""The shared retry mechanism (hardware_monitor/retry.py) that the GitHub
client, the log writes and the CSV report all use. The wait between attempts
is a mock (the autouse `retry_waits` fixture in conftest.py), so no test
really sleeps; the waits it would have made are recorded instead."""

import pytest

from hardware_monitor.retry import retry


class Flaky:
    """An operation that fails with `errors` in order, then returns "done"."""

    def __init__(self, *errors):
        self.errors, self.calls = list(errors), 0

    def __call__(self):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "done"


def run(operation, max_attempts=3, retry_on=(TimeoutError,)):
    return retry(operation, retry_on=retry_on, max_attempts=max_attempts, backoff_seconds=0.5, label="Saving")


def test_a_success_is_returned_with_no_wait(retry_waits):
    operation = Flaky()
    assert run(operation) == "done"
    assert operation.calls == 1 and retry_waits == []


def test_recovers_after_temporary_failures_with_a_doubling_wait(retry_waits):
    operation = Flaky(TimeoutError("busy"), TimeoutError("still busy"))
    assert run(operation) == "done"
    assert operation.calls == 3 and retry_waits == [0.5, 1.0]


def test_gives_up_with_the_last_error_and_no_wait_after_the_final_attempt(retry_waits):
    operation = Flaky(TimeoutError("first"), TimeoutError("second"), TimeoutError("last"))
    with pytest.raises(TimeoutError, match="last"):
        run(operation)
    assert operation.calls == 3 and retry_waits == [0.5, 1.0]


def test_an_error_not_listed_as_temporary_is_raised_at_once(retry_waits):
    operation = Flaky(ValueError("would fail every time"))
    with pytest.raises(ValueError):
        run(operation)
    assert operation.calls == 1 and retry_waits == []


def test_a_subclass_of_a_temporary_error_is_retried(retry_waits):
    operation = Flaky(PermissionError(13, "locked"))
    assert run(operation, retry_on=(OSError,)) == "done"
    assert operation.calls == 2


def test_each_retry_is_reported_while_it_happens(capsys):
    run(Flaky(TimeoutError("busy")))
    assert capsys.readouterr().out == "Saving attempt 1/3 failed (busy) Retrying in 0.5 s.\n"


@pytest.mark.parametrize("max_attempts", [0, -1])
def test_an_attempt_limit_below_one_is_rejected(max_attempts):
    operation = Flaky()
    with pytest.raises(ValueError, match="at least 1"):
        run(operation, max_attempts=max_attempts)
    assert operation.calls == 0
