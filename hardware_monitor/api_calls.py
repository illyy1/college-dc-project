"""What the two web API clients (github_client, gemini_client) share: how a
failed request is described, which HTTP errors are worth retrying, and the
retry loop around one attempt. Each client keeps its own request and the
reading of its reply."""

import requests

from .retry import retry

# rate limited / over the quota, or a server-side problem: likely to work if tried again a bit later
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def request_failure(error, service, timeout):
    """(message, worth retrying) for a requests exception raised by one attempt.

    Most specific first: ConnectTimeout is both a Timeout and a
    ConnectionError, and every requests error is a RequestException. Only the
    exception's name is reported: requests quotes the headers or URL in its
    messages, and with them the key."""
    if isinstance(error, requests.exceptions.Timeout):
        return f"{service} did not answer within {timeout} s.", True
    if isinstance(error, requests.exceptions.ConnectionError):  # DNS failure, refused/reset connection, SSL, proxy
        return f"Could not connect to {service} ({type(error).__name__}).", True
    # any other request problem (redirects, a bad URL or header, ...): only a reply
    # cut off mid-way may work next time; the rest would fail the same way again
    return f"{service} request failed ({type(error).__name__}).", \
        isinstance(error, requests.exceptions.ChunkedEncodingError)


def retry_api_call(try_once, *, max_attempts, backoff_seconds, label):
    """Calls try_once() until it succeeds or fails for good, through retry().
    try_once() returns (result, worth retrying); result has .ok, .error and
    .attempts, which is set here. Returns the last result: after the final
    failed attempt, its error says how many attempts were made."""
    attempts = 0

    def attempt():
        nonlocal attempts
        attempts += 1
        outcome, retryable = try_once()
        outcome.attempts = attempts
        if not outcome.ok and retryable:
            raise _TemporaryFailure(outcome)
        return outcome

    try:
        return retry(attempt, retry_on=_TemporaryFailure, max_attempts=max_attempts,
                     backoff_seconds=backoff_seconds, label=label)
    except _TemporaryFailure as failure:
        result = failure.result
        result.error = f"Gave up after {max_attempts} attempt(s). Last error: {result.error}"
        return result


class _TemporaryFailure(Exception):
    """A failed attempt worth retrying; carries its result so the last one
    can be returned once retry() gives up."""

    def __init__(self, result):
        super().__init__(result.error)
        self.result = result
