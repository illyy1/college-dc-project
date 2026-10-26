"""Gemini client (Google AI Studio) the dashboard uses to add troubleshooting
suggestions to the GitHub issue it opens for a hardware fault. Kept apart
from the Tkinter code, like github_client, so the API handling (success,
HTTP errors, timeouts, blocked or empty replies, retries) can be tested
without a window or a network.

`post` is imported by name, so tests can fake Gemini apart from GitHub's
requests.post."""

from dataclasses import dataclass

import requests
from requests import post

from .api_calls import RETRYABLE_STATUS_CODES, request_failure, retry_api_call

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta"
# Flash-Lite: answers in about 2 s; on 2026-10-26 the larger Flash models timed out or were "under high demand"
GEMINI_MODEL = "gemini-3.1-flash-lite"
REQUEST_TIMEOUT_SECONDS = 10  # a model takes longer to answer than GitHub
MAX_SUGGESTION_CHARS = 3000   # the text goes into a GitHub issue: a runaway reply can't flood it

# fewer than GitHub's: the issue waits for the suggestions, and they are optional
MAX_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 1


@dataclass
class SuggestionResult:
    ok: bool
    text: str | None = None
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
        "For the technician who will handle it, list the most likely causes and "
        "up to five short troubleshooting steps, most likely fix first. Plain "
        "text or simple Markdown, no introduction."
    )


def suggest_fixes(api_key, title, details, component, timeout=REQUEST_TIMEOUT_SECONDS, max_attempts=MAX_ATTEMPTS):
    """Asks Gemini for troubleshooting suggestions for a fault, retrying
    temporary failures (timeouts, connection errors, HTTP 429/5xx) up to
    max_attempts times. Permanent failures (bad key, unknown model) and
    replies with no usable text (e.g. a blocked prompt) are not retried.

    Never raises for API problems: every failure comes back as
    SuggestionResult(ok=False, error=...), and the error never quotes the key."""
    if not api_key:
        return SuggestionResult(False, error="Gemini API key missing.", attempts=0)

    prompt = build_prompt(title, details, component)
    return retry_api_call(lambda: _try_once(api_key, prompt, timeout),
                          max_attempts=max_attempts, backoff_seconds=RETRY_BACKOFF_SECONDS, label="Gemini")


def _try_once(api_key, prompt, timeout):
    """One POST. Returns (SuggestionResult, whether the failure is worth retrying)."""
    try:
        response = post(
            f"{GEMINI_API_URL}/models/{GEMINI_MODEL}:generateContent",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            # the key in a header, not the URL: URLs end up in logs and error messages
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        error, retryable = request_failure(e, "Gemini", timeout)
        return SuggestionResult(False, error=error), retryable

    if response.status_code != 200:
        error = f"Gemini returned HTTP {response.status_code}{_api_message(response)}."
        return SuggestionResult(False, error=error), response.status_code in RETRYABLE_STATUS_CODES
    return _parse_suggestion(response), False


def _api_message(response):
    """Gemini explains errors in {"error": {"message": ...}}; returns ': <message>' or ''."""
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):  # not JSON, or not shaped like an error
        return ""
    return f": {message}" if message else ""


def _parse_suggestion(response):
    try:
        data = response.json()
    except ValueError:
        return SuggestionResult(False, error="Gemini's reply was not valid JSON.")
    if not isinstance(data, dict):
        return SuggestionResult(False, error="Gemini's reply was not a JSON object.")

    blocked = (data.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        return SuggestionResult(False, error=f"Gemini blocked the prompt ({blocked}).")

    text = _candidate_text(data.get("candidates")).strip()
    if not text:
        return SuggestionResult(False, error="Gemini's reply had no suggestion.")
    return SuggestionResult(True, text=text[:MAX_SUGGESTION_CHARS].rstrip())


def _candidate_text(candidates):
    """The first candidate's text parts, joined; "" if the reply has none."""
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        return ""
    parts = (candidates[0].get("content") or {}).get("parts")
    if not isinstance(parts, list):
        return ""
    return "".join(part["text"] for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str))
