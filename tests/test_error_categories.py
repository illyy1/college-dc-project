"""TDD specification for error categorization in hardware_monitor/api_client.py:
every failed API call says explicitly which of four kinds of failure it was,
so the code that receives it can tell them apart without reading the text.

What the module must provide:
- ErrorCategory, an Enum with exactly these members:
    COMMUNICATION  the request never got a reply (timeout, connection
                   error, any other requests error)
    HTTP           a reply came, with an error or unexpected status code
    INVALID_JSON   the reply, or Gemini's answer inside it, is not JSON
    VALIDATION     the JSON parsed but its structure or values are wrong
                   (not an object, a missing or impossible field, a blocked
                   or unfinished Gemini answer)
- IssueResult.category and TriageResult.category: the ErrorCategory of a
  failure; None on success, and None when nothing was sent (no token or key:
  a set-up problem, not an API failure).
- After the retries run out, the result keeps the last attempt's category.

Only the external dependencies are mocked (the `github` and `gemini`
fixtures, and the sleep between retries); create_issue and triage_fault run
for real."""

import pytest
import requests

from fake_gemini import answer, api_error, triage_answer
from fake_http import NOT_JSON, api_response
from hardware_monitor import api_client
from hardware_monitor.api_client import create_issue, triage_fault


def category(name):
    return api_client.ErrorCategory[name]


def send_issue(token="secret-token", **kwargs):
    return create_issue(token, "owner/repo", "Camera down", "details", **kwargs)


def ask_gemini(api_key="secret-gemini-key", **kwargs):
    return triage_fault(api_key, "Camera down", "No frames for 30 s", "camera", **kwargs)


def test_there_are_exactly_four_categories():
    assert [c.name for c in api_client.ErrorCategory] == ["COMMUNICATION", "HTTP", "INVALID_JSON", "VALIDATION"]


# --- GitHub ---

@pytest.mark.parametrize("reply, expected", [
    (requests.exceptions.ReadTimeout(), "COMMUNICATION"),
    (requests.exceptions.ConnectTimeout(), "COMMUNICATION"),
    (requests.exceptions.ConnectionError(), "COMMUNICATION"),
    (requests.exceptions.TooManyRedirects(), "COMMUNICATION"),
    (requests.exceptions.ChunkedEncodingError(), "COMMUNICATION"),
    (api_response(401, {"message": "Bad credentials"}), "HTTP"),
    (api_response(404, {"message": "Not Found"}), "HTTP"),
    (api_response(500, text="<html>oops</html>"), "HTTP"),
    (api_response(200, {"number": 1, "html_url": "https://github.com/o/r/issues/1"}), "HTTP"),
    (api_response(201, text="<html>not json</html>"), "INVALID_JSON"),
    (api_response(201, NOT_JSON, text=""), "INVALID_JSON"),
    (api_response(201, ["a", "list"]), "VALIDATION"),
    (api_response(201, {"html_url": "https://github.com/o/r/issues/1"}), "VALIDATION"),
    (api_response(201, {"number": "1", "html_url": "https://github.com/o/r/issues/1"}), "VALIDATION"),
    (api_response(201, {"number": 0, "html_url": "https://github.com/o/r/issues/0"}), "VALIDATION"),
    (api_response(201, {"number": 7, "html_url": "https://github.com/o/r/issues/8"}), "VALIDATION"),
], ids=["read-timeout", "connect-timeout", "connection-refused", "redirect-loop", "reply-cut-off",
        "401-bad-token", "404-no-repo", "500-server-error", "200-instead-of-201",
        "html-body", "empty-body",
        "json-list", "missing-number", "number-as-text", "number-zero", "link-to-another-issue"])
def test_github_failures_are_categorized(github, reply, expected):
    github.reply = reply
    result = send_issue(max_attempts=1)
    assert not result.ok
    assert result.category is category(expected)


def test_github_success_has_no_category(github):
    assert send_issue().category is None


def test_github_without_a_token_has_no_category(github):
    result = send_issue(token=None)
    assert not result.ok and result.category is None


@pytest.mark.parametrize("reply, expected", [
    (requests.exceptions.ReadTimeout(), "COMMUNICATION"),
    (api_response(503, text="busy"), "HTTP"),
], ids=["timeouts", "503s"])
def test_github_keeps_the_category_after_the_retries_run_out(github, retry_waits, reply, expected):
    github.reply = reply
    result = send_issue(max_attempts=3)
    assert result.attempts == 3
    assert result.category is category(expected)


def test_github_reports_the_last_attempts_category(github, retry_waits):
    github.reply = [requests.exceptions.ReadTimeout(), api_response(201, {"number": 1})]
    assert send_issue(max_attempts=3).category is category("VALIDATION")


# --- Gemini ---

@pytest.mark.parametrize("reply, expected", [
    (requests.exceptions.ReadTimeout(), "COMMUNICATION"),
    (requests.exceptions.ConnectionError(), "COMMUNICATION"),
    (requests.exceptions.InvalidHeader(), "COMMUNICATION"),
    (api_error(400, "API key not valid."), "HTTP"),
    (api_error(404, "models/x is not found."), "HTTP"),
    (api_error(503, "The model is overloaded."), "HTTP"),
    (api_response(204, text=""), "HTTP"),
    (api_response(200, text="<html>not json</html>"), "INVALID_JSON"),
    (answer("Reseat the cable, it is probably loose."), "INVALID_JSON"),
    (api_response(200, ["a", "list"]), "VALIDATION"),
    (answer("[1, 2]"), "VALIDATION"),
    (api_response(200, {"promptFeedback": {"blockReason": "SAFETY"}}), "VALIDATION"),
    (api_response(200, {"candidates": []}), "VALIDATION"),
    (triage_answer(finish="MAX_TOKENS"), "VALIDATION"),
    (triage_answer(finish=None), "VALIDATION"),
    (triage_answer(cause=None), "VALIDATION"),
    (triage_answer(priority=9), "VALIDATION"),
    (triage_answer(steps=[]), "VALIDATION"),
], ids=["read-timeout", "connection-refused", "bad-header",
        "400-bad-key", "404-unknown-model", "503-overloaded", "204-instead-of-200",
        "html-body", "answer-is-prose",
        "json-list", "answer-is-a-list", "prompt-blocked", "no-candidates", "cut-off-at-max-tokens",
        "no-finish-reason", "missing-cause", "priority-out-of-range", "no-steps"])
def test_gemini_failures_are_categorized(gemini, reply, expected):
    gemini.reply = reply
    result = ask_gemini(max_attempts=1)
    assert not result.ok
    assert result.category is category(expected)


def test_gemini_success_has_no_category(gemini):
    assert ask_gemini().category is None


def test_gemini_without_a_key_has_no_category(gemini):
    result = ask_gemini(api_key=None)
    assert not result.ok and result.category is None


@pytest.mark.parametrize("reply, expected", [
    (requests.exceptions.ConnectionError(), "COMMUNICATION"),
    (api_error(429, "Quota exceeded."), "HTTP"),
], ids=["connection-errors", "429s"])
def test_gemini_keeps_the_category_after_the_retries_run_out(gemini, retry_waits, reply, expected):
    gemini.reply = reply
    result = ask_gemini(max_attempts=2)
    assert result.attempts == 2
    assert result.category is category(expected)
