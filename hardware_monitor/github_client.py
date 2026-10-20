"""GitHub Issues client the dashboard uses to report hardware faults. Kept
apart from the Tkinter code so the API handling (success, HTTP errors,
timeouts, malformed replies, retries) can be tested without a window or a
network."""

import time
from dataclasses import dataclass

import requests

GITHUB_API_URL = "https://api.github.com"
REQUEST_TIMEOUT_SECONDS = 5
# fields of GitHub's "issue created" reply the dashboard relies on, with their types
REQUIRED_RESPONSE_FIELDS = {"number": int, "html_url": str}

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1  # waits 1 s, then 2 s, 4 s, ... between attempts
# rate limited or a server-side problem: likely to work if tried again a bit later
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_sleep = time.sleep  # replaced in tests so retries don't really wait


@dataclass
class IssueResult:
    ok: bool
    issue_number: int | None = None
    url: str | None = None
    error: str | None = None
    attempts: int = 1


def create_issue(token, repo, title, body, labels=(), timeout=REQUEST_TIMEOUT_SECONDS, max_attempts=MAX_ATTEMPTS):
    """Opens a GitHub issue, retrying temporary failures (timeouts, network
    errors, HTTP 429/5xx) up to max_attempts times with a growing wait.
    Permanent failures (bad token, missing repo, invalid request) and
    incomplete replies are not retried: the first would fail again, and an
    incomplete 201 reply means the issue was already created.

    A timeout can hide a request GitHub did receive, so a retry after one may
    occasionally post a duplicate issue; that is preferred to losing the alert.

    Never raises for API problems: every failure comes back as
    IssueResult(ok=False, error=...), so a failed alert can't crash the
    monitoring thread that sent it."""
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be at least 1, got {max_attempts}.")
    if not token:
        return IssueResult(False, error="GitHub token missing or decryption failed.", attempts=0)

    for attempt in range(1, max_attempts + 1):
        result, retryable = _try_once(token, repo, title, body, labels, timeout)
        result.attempts = attempt
        if result.ok or not retryable:
            return result
        if attempt < max_attempts:
            delay = RETRY_BACKOFF_SECONDS * 2 ** (attempt - 1)
            print(f"GitHub attempt {attempt}/{max_attempts} failed ({result.error}) Retrying in {delay} s.")
            _sleep(delay)

    result.error = f"Gave up after {max_attempts} attempt(s). Last error: {result.error}"
    return result


def _try_once(token, repo, title, body, labels, timeout):
    """One POST. Returns (IssueResult, whether the failure is worth retrying)."""
    try:
        response = requests.post(
            f"{GITHUB_API_URL}/repos/{repo}/issues",
            json={"title": title, "body": body, "labels": list(labels)},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"},
            timeout=timeout,
        )
    except requests.exceptions.Timeout:
        return IssueResult(False, error=f"GitHub did not answer within {timeout} s."), True
    except requests.exceptions.RequestException as e:
        return IssueResult(False, error=f"Network error: {e}"), True

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
