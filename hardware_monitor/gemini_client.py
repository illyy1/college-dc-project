"""Gemini client (Google AI Studio) the dashboard uses to triage each
hardware fault: a priority, the likely cause, an estimated repair time and
troubleshooting steps, as checked JSON. The triage drives the system: it can
raise the call's priority, labels the GitHub issue and feeds the maintenance
report. Kept apart from the Tkinter code, like github_client, so the API
handling (success, HTTP errors, timeouts, blocked or invalid answers,
retries) can be tested without a window or a network.

`post` is imported by name, so tests can fake Gemini apart from GitHub's
requests.post."""

import json
from dataclasses import dataclass

import requests
from requests import post

from .api_calls import json_object, request_failure, retry_api_call, status_failure

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta"
# Flash-Lite: answers in about 2 s; on 2026-10-26 the larger Flash models timed out or were "under high demand"
GEMINI_MODEL = "gemini-3.1-flash-lite"
REQUEST_TIMEOUT_SECONDS = 10  # a model takes longer to answer than GitHub

# fewer than GitHub's: the issue waits for the triage, and it is optional
MAX_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 1

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


def triage_fault(api_key, title, details, component, timeout=REQUEST_TIMEOUT_SECONDS, max_attempts=MAX_ATTEMPTS):
    """Asks Gemini to triage a fault, retrying temporary failures (timeouts,
    connection errors, HTTP 429/5xx) up to max_attempts times. Permanent
    failures (bad key, unknown model) and answers that can't be used (a
    blocked prompt, an invalid triage) are not retried.

    Never raises for API problems: every failure comes back as
    TriageResult(ok=False, error=...), and the error never quotes the key."""
    if not api_key:
        return TriageResult(False, error="Gemini API key missing.", attempts=0)

    prompt = build_prompt(title, details, component)
    return retry_api_call(lambda: _try_once(api_key, prompt, timeout),
                          max_attempts=max_attempts, backoff_seconds=RETRY_BACKOFF_SECONDS, label="Gemini")


def _try_once(api_key, prompt, timeout):
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
        error, retryable = request_failure(e, "Gemini", timeout)
        return TriageResult(False, error=error), retryable

    failure = status_failure(response, "Gemini", 200, _api_message)  # an answer is 200 OK
    if failure:
        error, retryable = failure
        return TriageResult(False, error=error), retryable
    return _parse_reply(response), False


def _api_message(response):
    """Gemini explains errors in {"error": {"message": ...}}; returns ': <message>' or ''."""
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):  # not JSON, or not shaped like an error
        return ""
    return f": {message}" if message else ""


def _parse_reply(response):
    """The reply's envelope: not blocked, one candidate with text, finished."""
    data, error = json_object(response, "Gemini")
    if error:
        return TriageResult(False, error=error)

    blocked = (data.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        return TriageResult(False, error=f"Gemini blocked the prompt ({blocked}).")

    candidate = _first_candidate(data.get("candidates"))
    text = _candidate_text(candidate).strip()
    if not text:
        return TriageResult(False, error="Gemini's reply had no answer.")

    finish = candidate.get("finishReason")
    if finish is None:
        return TriageResult(False, error="Gemini's reply is incomplete: missing 'finishReason'.")
    if finish != "STOP":
        # cut off at the length limit or stopped for safety: the JSON may be half-written
        return TriageResult(False, error=f"Gemini stopped before finishing the triage ({finish}).")
    return _parse_triage(text)


def _parse_triage(text):
    """The answer itself: a JSON object with every triage field valid."""
    try:
        answer = json.loads(text)
    except ValueError:
        return TriageResult(False, error="Gemini's answer was not valid JSON.")
    if not isinstance(answer, dict):
        return TriageResult(False, error="Gemini's answer was not a JSON object.")

    problems = _triage_problems(answer)
    if problems:
        return TriageResult(False, error=f"Gemini's triage is invalid: {', '.join(problems)}.")
    steps = tuple(step.strip()[:MAX_STEP_CHARS].rstrip() for step in answer["steps"][:MAX_STEPS])
    return TriageResult(True, triage=Triage(answer["priority"], answer["cause"], answer["repair_minutes"], steps))


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
