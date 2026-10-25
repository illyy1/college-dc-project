"""The one retry mechanism the system uses: the GitHub client, the log writes
and the CSV report all retry through retry(), so the attempt limit, the
doubling wait and what is reported behave the same everywhere. Each caller
decides which of its errors are temporary."""

from time import sleep


def retry(operation, *, retry_on, max_attempts, backoff_seconds, label):
    """Calls operation() and returns what it returns. An exception listed in
    retry_on (a temporary failure) is tried again, up to max_attempts tries in
    all, waiting backoff_seconds, then twice that, and so on in between; each
    retry is printed as "<label> attempt n/max failed (<error>) Retrying in s s."
    Any other exception, and the last temporary one, is raised: there is no
    wait after the final attempt."""
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be at least 1, got {max_attempts}.")

    for attempt in range(1, max_attempts):
        try:
            return operation()
        except retry_on as error:
            delay = backoff_seconds * 2 ** (attempt - 1)
            print(f"{label} attempt {attempt}/{max_attempts} failed ({error}) Retrying in {delay} s.")
            sleep(delay)
    return operation()  # the final attempt: whatever it raises is raised
