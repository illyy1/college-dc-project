"""GitHub Issues client the dashboard uses to report hardware faults. Kept
apart from the Tkinter code so the API handling (success, HTTP errors,
timeouts, malformed replies, retries) can be tested without a window or a
network."""

from dataclasses import dataclass

import requests

from .retry import retry

GITHUB_API_URL = "https://api.github.com"
REQUEST_TIMEOUT_SECONDS = 5
# fields of GitHub's "issue created" reply the dashboard relies on, with their types
REQUIRED_RESPONSE_FIELDS = {"number": int, "html_url": str}

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1  # waits 1 s, then 2 s, 4 s, ... between attempts
# rate limited or a server-side problem: likely to work if tried again a bit later
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass
class IssueResult:
    ok: bool
    issue_number: int | None = None
    url: str | None = None
    error: str | None = None
    attempts: int = 1


def create_issue(token, repo, title, body, labels=(), timeout=REQUEST_TIMEOUT_SECONDS, max_attempts=MAX_ATTEMPTS):
    """Opens a GitHub issue, retrying temporary failures (timeouts, connection
    errors, a reply cut off mid-way, HTTP 429/5xx) up to max_attempts times with
    a growing wait. Permanent failures (bad token, missing repo, invalid request,
    a bad URL or header, a redirect loop) and
    incomplete replies are not retried: the first would fail again, and an
    incomplete 201 reply means the issue was already created.

    A timeout can hide a request GitHub did receive, so a retry after one may
    occasionally post a duplicate issue; that is preferred to losing the alert.

    Never raises for API problems: every failure comes back as
    IssueResult(ok=False, error=...), so a failed alert can't crash the
    monitoring thread that sent it."""
    if not token:
        return IssueResult(False, error="GitHub token missing or decryption failed.", attempts=0)

    attempts = 0

    def attempt():
        nonlocal attempts
        attempts += 1
        outcome, retryable = _try_once(token, repo, title, body, labels, timeout)
        outcome.attempts = attempts
        if not outcome.ok and retryable:
            raise _TemporaryFailure(outcome)
        return outcome

    try:
        return retry(attempt, retry_on=_TemporaryFailure, max_attempts=max_attempts,
                     backoff_seconds=RETRY_BACKOFF_SECONDS, label="GitHub")
    except _TemporaryFailure as failure:
        result = failure.result
        result.error = f"Gave up after {max_attempts} attempt(s). Last error: {result.error}"
        return result


class _TemporaryFailure(Exception):
    """A failed attempt worth retrying; carries its IssueResult so the last
    one can be returned once retry() gives up."""

    def __init__(self, result):
        super().__init__(result.error)
        self.result = result


def _try_once(token, repo, title, body, labels, timeout):
    """One POST. Returns (IssueResult, whether the failure is worth retrying)."""
    try:
        response = requests.post(
            f"{GITHUB_API_URL}/repos/{repo}/issues",
            json={"title": title, "body": body, "labels": list(labels)},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"},
            timeout=timeout,
        )
    # most specific first: ConnectTimeout is both a Timeout and a ConnectionError,
    # and every requests error is a RequestException. Only the exception's name is
    # reported: requests quotes the headers or URL in its messages, and with them the key.
    except requests.exceptions.Timeout:
        return IssueResult(False, error=f"GitHub did not answer within {timeout} s."), True
    except requests.exceptions.ConnectionError as e:  # DNS failure, refused/reset connection, SSL, proxy
        return IssueResult(False, error=f"Could not connect to GitHub ({type(e).__name__})."), True
    except requests.exceptions.RequestException as e:  # any other request problem: redirects, a bad URL or header, ...
        # only a reply cut off mid-way may work next time; the rest would fail the same way again
        retryable = isinstance(e, requests.exceptions.ChunkedEncodingError)
        return IssueResult(False, error=f"GitHub request failed ({type(e).__name__})."), retryable

    if response.status_code != 201:
        error = f"GitHub returned HTTP {response.status_code}{_api_message(response)}."
        return IssueResult(False, error=error), response.status_code in RETRYABLE_STATUS_CODES
    return _parse_created_issue(response), False


def _api_message(response):
    """GitHub explains errors in a JSON "message" field; returns ': <message>' or ''."""
    try:
        message = response.json().get("message")
    except (ValueError, AttributeError):  # not JSON, or JSON that is not an object
        return ""
    return f": {message}" if message else ""


def _parse_created_issue(response):
    try:
        data = response.json()
    except ValueError:
        return IssueResult(False, error="GitHub's reply was not valid JSON.")
    if not isinstance(data, dict):
        return IssueResult(False, error="GitHub's reply was not a JSON object.")

    problems = []
    for field, expected in REQUIRED_RESPONSE_FIELDS.items():
        value = data.get(field)
        if value is None:
            problems.append(f"missing '{field}'")
        elif isinstance(value, bool) or not isinstance(value, expected):
            problems.append(f"'{field}' should be {expected.__name__}, got {value!r}")
    if problems:
        return IssueResult(False, error=f"GitHub's reply is incomplete: {', '.join(problems)}.")

    return IssueResult(True, issue_number=data["number"], url=data["html_url"])
