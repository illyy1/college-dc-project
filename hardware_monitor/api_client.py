"""Raw network communication, and only that: the one module that talks to
the network. It sends the requests to GitHub Issues (create_issue) and to
Gemini (triage_fault), checks each reply (the HTTP status, the JSON, every
field) and turns it into a plain result: IssueResult, or TriageResult with
a Triage. A failed result names its ErrorCategory: no reply
(COMMUNICATION), an error status (HTTP), a reply that isn't JSON
(INVALID_JSON), or JSON with the wrong structure or values (VALIDATION).
It decides nothing about the business: what to do with a result
(the call's priority, the issue's text and labels, the report) is in
services.py. From the package it uses only the generic retry loop.

GitHub is reached through requests.post and Gemini through `post`, imported
by name, so tests can fake each service on its own."""

import json
from dataclasses import dataclass
from enum import Enum

import requests
from requests import post

from .retry import retry

# rate limited / over the quota, or a server-side problem: likely to work if tried again a bit later
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class ErrorCategory(Enum):
    """Which kind of failure a failed API call was."""
    COMMUNICATION = "communication"  # no reply: timeout, connection error, any other requests error
    HTTP = "http"                    # a reply with an error or unexpected status code
    INVALID_JSON = "invalid json"    # the reply, or Gemini's answer inside it, is not JSON
    VALIDATION = "validation"        # JSON whose structure or values are wrong


# ======================================================================
# Shared by both services: describing a failure, checking a reply, retrying
# ======================================================================

def request_failure(error, service, timeout):
    """(message, ErrorCategory.COMMUNICATION, worth retrying) for a requests
    exception raised by one attempt.

    Most specific first: ConnectTimeout is both a Timeout and a
    ConnectionError, and every requests error is a RequestException. Only the
    exception's name is reported: requests quotes the headers or URL in its
    messages, and with them the key."""
    if isinstance(error, requests.exceptions.Timeout):
        return f"{service} did not answer within {timeout} s.", ErrorCategory.COMMUNICATION, True
    if isinstance(error, requests.exceptions.ConnectionError):  # DNS failure, refused/reset connection, SSL, proxy
        return f"Could not connect to {service} ({type(error).__name__}).", ErrorCategory.COMMUNICATION, True
    # any other request problem (redirects, a bad URL or header, ...): only a reply
    # cut off mid-way may work next time; the rest would fail the same way again
    return f"{service} request failed ({type(error).__name__}).", ErrorCategory.COMMUNICATION, \
        isinstance(error, requests.exceptions.ChunkedEncodingError)


def status_failure(response, service, expected_status, api_message):
    """(message, ErrorCategory.HTTP, worth retrying) if the reply's status
    isn't expected_status, else None. The status is checked with requests' raise_for_status() (any
    4xx/5xx); it lets every other code through, so the exact success code is
    checked after it. api_message(response) gives the API's own explanation,
    as ': <message>' or ''."""
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError:
        return (f"{service} returned HTTP {response.status_code}{api_message(response)}.", ErrorCategory.HTTP,
                response.status_code in RETRYABLE_STATUS_CODES)
    if response.status_code != expected_status:
        return f"{service} returned HTTP {response.status_code}{api_message(response)}.", ErrorCategory.HTTP, False
    return None


def json_object(response, service):
    """(the reply's JSON object, None, None), or (None, what is wrong with it, its ErrorCategory)."""
    try:
        data = response.json()
    except ValueError:
        return None, f"{service}'s reply was not valid JSON.", ErrorCategory.INVALID_JSON
    if not isinstance(data, dict):
        return None, f"{service}'s reply was not a JSON object.", ErrorCategory.VALIDATION
    return data, None, None


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


# ======================================================================
# GitHub Issues
# ======================================================================

GITHUB_API_URL = "https://api.github.com"
GITHUB_TIMEOUT_SECONDS = 5
GITHUB_MAX_ATTEMPTS = 3
GITHUB_RETRY_BACKOFF_SECONDS = 1  # waits 1 s, then 2 s, 4 s, ... between attempts
# fields of GitHub's "issue created" reply the program relies on, with their types
REQUIRED_RESPONSE_FIELDS = {"number": int, "html_url": str}


@dataclass
class IssueResult:
    ok: bool
    issue_number: int | None = None
    url: str | None = None
    error: str | None = None
    attempts: int = 1
    category: ErrorCategory | None = None  # None on success, or when nothing was sent


def create_issue(token, repo, title, body, labels=(), timeout=GITHUB_TIMEOUT_SECONDS,
                 max_attempts=GITHUB_MAX_ATTEMPTS):
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

    return retry_api_call(lambda: _github_try_once(token, repo, title, body, labels, timeout),
                          max_attempts=max_attempts, backoff_seconds=GITHUB_RETRY_BACKOFF_SECONDS, label="GitHub")


def _github_try_once(token, repo, title, body, labels, timeout):
    """One POST. Returns (IssueResult, whether the failure is worth retrying)."""
    try:
        response = requests.post(
            f"{GITHUB_API_URL}/repos/{repo}/issues",
            json={"title": title, "body": body, "labels": list(labels)},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"},
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        error, category, retryable = request_failure(e, "GitHub", timeout)
        return IssueResult(False, error=error, category=category), retryable

    failure = status_failure(response, "GitHub", 201, _github_message)  # a new issue is 201 Created
    if failure:
        error, category, retryable = failure
        return IssueResult(False, error=error, category=category), retryable
    return _parse_created_issue(response), False


def _github_message(response):
    """GitHub explains errors in a JSON "message" field; returns ': <message>' or ''."""
    try:
        message = response.json().get("message")
    except (ValueError, AttributeError):  # not JSON, or JSON that is not an object
        return ""
    return f": {message}" if message else ""


def _parse_created_issue(response):
    data, error, category = json_object(response, "GitHub")
    if error:
        return IssueResult(False, error=error, category=category)

    problems = []
    for field, expected in REQUIRED_RESPONSE_FIELDS.items():
        value = data.get(field)
        if value is None:
            problems.append(f"missing '{field}'")
        elif isinstance(value, bool) or not isinstance(value, expected):
            problems.append(f"'{field}' should be {expected.__name__}, got {value!r}")
    if problems:
        return _invalid_issue(f"GitHub's reply is incomplete: {', '.join(problems)}.")

    problems = _impossible_issue_values(data["number"], data["html_url"])
    if problems:
        return _invalid_issue(f"GitHub's reply is invalid: {', '.join(problems)}.")
    return IssueResult(True, issue_number=data["number"], url=data["html_url"])


def _invalid_issue(error):
    """A GitHub reply whose JSON has the wrong structure or values."""
    return IssueResult(False, error=error, category=ErrorCategory.VALIDATION)


def _impossible_issue_values(number, url):
    """What is wrong with a reply's values, once their types are right: the
    number and link are shown to the user, so both must point at the new issue."""
    problems = []
    if number <= 0:
        problems.append(f"'number' should be positive, got {number}")
    if not url.strip():
        problems.append("'html_url' is empty")
    elif not url.startswith("https://"):
        problems.append("'html_url' should be an https link")
    elif not url.rstrip("/").endswith(f"/issues/{number}"):
        problems.append(f"'html_url' doesn't link to issue #{number}")
    return problems


# --- the open issues: so the same fault isn't posted twice ---

@dataclass(frozen=True)
class OpenIssue:
    number: int
    url: str
    title: str


@dataclass
class IssueSearchResult:
    ok: bool
    issues: tuple = ()  # OpenIssue, in GitHub's order
    error: str | None = None
    attempts: int = 1
    category: ErrorCategory | None = None  # None on success, or when nothing was sent


def find_open_issues(token, repo, labels, timeout=GITHUB_TIMEOUT_SECONDS, max_attempts=GITHUB_MAX_ATTEMPTS):
    """GETs the open issues that have every one of `labels` (pull requests
    left out). A Timeout or ConnectionError (or HTTP 429/5xx) is retried, up
    to max_attempts attempts in all, waiting 1 s, then 2 s, ... between them;
    permanent errors are not. Never raises for API problems: every failure
    comes back as IssueSearchResult(ok=False, error=..., category=...)."""
    if not token:
        return IssueSearchResult(False, error="GitHub token missing or decryption failed.", attempts=0)

    return retry_api_call(lambda: _search_try_once(token, repo, labels, timeout),
                          max_attempts=max_attempts, backoff_seconds=GITHUB_RETRY_BACKOFF_SECONDS,
                          label="GitHub lookup")


def _search_try_once(token, repo, labels, timeout):
    """One GET. Returns (IssueSearchResult, whether the failure is worth retrying)."""
    try:
        response = requests.get(
            f"{GITHUB_API_URL}/repos/{repo}/issues",
            params={"state": "open", "labels": ",".join(labels), "per_page": 100},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"},
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        error, category, retryable = request_failure(e, "GitHub", timeout)
        return IssueSearchResult(False, error=error, category=category), retryable

    failure = status_failure(response, "GitHub", 200, _github_message)  # a list is 200 OK
    if failure:
        error, category, retryable = failure
        return IssueSearchResult(False, error=error, category=category), retryable
    return _parse_open_issues(response), False


def _parse_open_issues(response):
    try:
        data = response.json()
    except ValueError:
        return IssueSearchResult(False, error="GitHub's reply was not valid JSON.",
                                 category=ErrorCategory.INVALID_JSON)
    if not isinstance(data, list):
        return _invalid_search("GitHub's list of open issues was not a JSON list.")

    issues = []
    for item in data:
        if not isinstance(item, dict):
            return _invalid_search(f"GitHub's list of open issues has an entry that is not an issue: {item!r}.")
        if "pull_request" in item:  # GitHub lists pull requests among the issues
            continue
        problems = []
        for field, expected in {**REQUIRED_RESPONSE_FIELDS, "title": str}.items():
            value = item.get(field)
            if value is None:
                problems.append(f"missing '{field}'")
            elif isinstance(value, bool) or not isinstance(value, expected):
                problems.append(f"'{field}' should be {expected.__name__}, got {value!r}")
        if not problems:
            problems = _impossible_issue_values(item["number"], item["html_url"])
        if problems:
            return _invalid_search(f"GitHub's list of open issues is invalid: {', '.join(problems)}.")
        issues.append(OpenIssue(item["number"], item["html_url"], item["title"]))
    return IssueSearchResult(True, issues=tuple(issues))


def _invalid_search(error):
    """A list of open issues whose JSON has the wrong structure or values."""
    return IssueSearchResult(False, error=error, category=ErrorCategory.VALIDATION)


# ======================================================================
# Gemini (Google AI Studio): a triage of a hardware fault, as checked JSON
# ======================================================================

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta"
# Flash-Lite: answers in about 2 s; on 2026-10-26 the larger Flash models timed out or were "under high demand"
GEMINI_MODEL = "gemini-3.1-flash-lite"
GEMINI_TIMEOUT_SECONDS = 10  # a model takes longer to answer than GitHub
# fewer than GitHub's: the issue waits for the triage, and it is optional
GEMINI_MAX_ATTEMPTS = 2
GEMINI_RETRY_BACKOFF_SECONDS = 1

# what a triage may say
CAUSES = ("cable", "driver", "hardware", "permissions", "software", "other")
MAX_STEPS = 5
MAX_STEP_CHARS = 300          # the steps go into a GitHub issue: a runaway answer can't flood it
MAX_REPAIR_MINUTES = 480      # a working day
# Gemini's JSON mode answers in this shape (an OpenAPI schema subset)
TRIAGE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "priority": {"type": "INTEGER", "description": "1 = most urgent, 5 = least urgent"},
        "cause": {"type": "STRING", "enum": list(CAUSES)},
        "repair_minutes": {"type": "INTEGER", "description": "estimated minutes to fix"},
        "steps": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "troubleshooting steps, best first"},
    },
    "required": ["priority", "cause", "repair_minutes", "steps"],
}


@dataclass(frozen=True)
class Triage:
    priority: int        # 1 (most urgent) to 5, like the system's own priorities
    cause: str           # one of CAUSES
    repair_minutes: int  # 1 to MAX_REPAIR_MINUTES
    steps: tuple         # 1 to MAX_STEPS texts


@dataclass
class TriageResult:
    ok: bool
    triage: Triage | None = None
    error: str | None = None
    attempts: int = 1
    category: ErrorCategory | None = None  # None on success, or when nothing was sent


def build_prompt(title, details, component):
    """The question for Gemini. Only the fault itself: nothing that identifies
    a person (the computer's name, the technician) is sent."""
    return (
        "A room hardware monitor detected this fault on a classroom computer.\n"
        f"Alert: {title}\n"
        f"Component: {component}\n"
        f"Details: {details}\n\n"
        "Triage it for the technician who will handle it: how urgent it is (priority 1 = most "
        "urgent to 5), the most likely cause, about how many minutes the fix takes, and up to "
        f"{MAX_STEPS} short troubleshooting steps, most likely fix first."
    )


def triage_fault(api_key, title, details, component, timeout=GEMINI_TIMEOUT_SECONDS,
                 max_attempts=GEMINI_MAX_ATTEMPTS):
    """Asks Gemini to triage a fault, retrying temporary failures (timeouts,
    connection errors, HTTP 429/5xx) up to max_attempts times. Permanent
    failures (bad key, unknown model) and answers that can't be used (a
    blocked prompt, an invalid triage) are not retried.

    Never raises for API problems: every failure comes back as
    TriageResult(ok=False, error=...), and the error never quotes the key."""
    if not api_key:
        return TriageResult(False, error="Gemini API key missing.", attempts=0)

    prompt = build_prompt(title, details, component)
    return retry_api_call(lambda: _gemini_try_once(api_key, prompt, timeout),
                          max_attempts=max_attempts, backoff_seconds=GEMINI_RETRY_BACKOFF_SECONDS, label="Gemini")


def _gemini_try_once(api_key, prompt, timeout):
    """One POST. Returns (TriageResult, whether the failure is worth retrying)."""
    try:
        response = post(
            f"{GEMINI_API_URL}/models/{GEMINI_MODEL}:generateContent",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "responseSchema": TRIAGE_SCHEMA},
            },
            # the key in a header, not the URL: URLs end up in logs and error messages
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        error, category, retryable = request_failure(e, "Gemini", timeout)
        return TriageResult(False, error=error, category=category), retryable

    failure = status_failure(response, "Gemini", 200, _gemini_message)  # an answer is 200 OK
    if failure:
        error, category, retryable = failure
        return TriageResult(False, error=error, category=category), retryable
    return _parse_gemini_reply(response), False


def _gemini_message(response):
    """Gemini explains errors in {"error": {"message": ...}}; returns ': <message>' or ''."""
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):  # not JSON, or not shaped like an error
        return ""
    return f": {message}" if message else ""


def _parse_gemini_reply(response):
    """The reply's envelope: not blocked, one candidate with text, finished."""
    data, error, category = json_object(response, "Gemini")
    if error:
        return TriageResult(False, error=error, category=category)

    blocked = (data.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        return _invalid_triage(f"Gemini blocked the prompt ({blocked}).")

    candidate = _first_candidate(data.get("candidates"))
    text = _candidate_text(candidate).strip()
    if not text:
        return _invalid_triage("Gemini's reply had no answer.")

    finish = candidate.get("finishReason")
    if finish is None:
        return _invalid_triage("Gemini's reply is incomplete: missing 'finishReason'.")
    if finish != "STOP":
        # cut off at the length limit or stopped for safety: the JSON may be half-written
        return _invalid_triage(f"Gemini stopped before finishing the triage ({finish}).")
    return _parse_triage(text)


def _parse_triage(text):
    """The answer itself: a JSON object with every triage field valid."""
    try:
        answer = json.loads(text)
    except ValueError:
        return TriageResult(False, error="Gemini's answer was not valid JSON.", category=ErrorCategory.INVALID_JSON)
    if not isinstance(answer, dict):
        return _invalid_triage("Gemini's answer was not a JSON object.")

    problems = _triage_problems(answer)
    if problems:
        return _invalid_triage(f"Gemini's triage is invalid: {', '.join(problems)}.")
    steps = tuple(step.strip()[:MAX_STEP_CHARS].rstrip() for step in answer["steps"][:MAX_STEPS])
    return TriageResult(True, triage=Triage(answer["priority"], answer["cause"], answer["repair_minutes"], steps))


def _invalid_triage(error):
    """A Gemini reply, or its answer, whose JSON has the wrong structure or values."""
    return TriageResult(False, error=error, category=ErrorCategory.VALIDATION)


def _triage_problems(answer):
    """What is missing or impossible in a triage answer, field by field."""
    problems = []
    for field in TRIAGE_SCHEMA["required"]:
        if answer.get(field) is None:
            problems.append(f"missing '{field}'")
    if problems:
        return problems

    if not _whole_number_between(answer["priority"], 1, 5):
        problems.append(f"'priority' should be 1 to 5, got {answer['priority']!r}")
    if answer["cause"] not in CAUSES:
        problems.append(f"'cause' should be one of {', '.join(CAUSES)}, got {answer['cause']!r}")
    if not _whole_number_between(answer["repair_minutes"], 1, MAX_REPAIR_MINUTES):
        problems.append(f"'repair_minutes' should be 1 to {MAX_REPAIR_MINUTES}, got {answer['repair_minutes']!r}")
    steps = answer["steps"]
    if not (isinstance(steps, list) and steps and all(isinstance(s, str) and s.strip() for s in steps)):
        problems.append(f"'steps' should be a list of texts (at least one), got {steps!r}")
    return problems


def _whole_number_between(value, low, high):
    # bool is an int in Python, but true/false is not a number here
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _first_candidate(candidates):
    """The first candidate of the reply, or {} if there is none."""
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        return {}
    return candidates[0]


def _candidate_text(candidate):
    """The candidate's text parts, joined; "" if it has none."""
    parts = (candidate.get("content") or {}).get("parts")
    if not isinstance(parts, list):
        return ""
    return "".join(part["text"] for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str))
