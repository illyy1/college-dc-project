"""TDD specification for hardware_monitor/gemini_client.py (not written yet):
AI-written troubleshooting suggestions, from Google's Gemini API, added to
the GitHub issue the dashboard opens for a hardware fault.

What the module must provide:
- suggest_fixes(api_key, title, details, component, timeout=..., max_attempts=...)
    -> SuggestionResult(ok, text=None, error=None, attempts=1)
    One POST to {GEMINI_API_URL}/models/{GEMINI_MODEL}:generateContent with
    the prompt from build_prompt(); the key goes in the x-goog-api-key
    header, never the URL. Temporary failures (timeouts, connection errors,
    HTTP 429/5xx) are retried through retry(). Never raises for API problems.
- build_prompt(title, details, component) -> str
- constants GEMINI_API_URL, GEMINI_MODEL, REQUEST_TIMEOUT_SECONDS,
  MAX_ATTEMPTS, RETRY_BACKOFF_SECONDS, MAX_SUGGESTION_CHARS

And hardware_monitor.config.get_gemini_api_key(): GEMINI_API_KEY from .env
(plain text), or None.

The dashboard: when it has a key, each GitHub issue gets the suggestions
under their own heading that says they are AI-generated. Without a key, or
when Gemini fails, the issue is posted as before. Nothing that identifies
a person (the computer's name, the technician) is sent to Gemini.

Only the external dependencies are mocked: Gemini's post (the `gemini`
fixture, fake_gemini.py), GitHub's requests.post (`github`) and the sleep
between retries. suggest_fixes and the dashboard run for real."""

import pytest
import requests

from fake_gemini import SUGGESTION, answer, api_error
from fake_github import NOT_JSON, api_response
from hardware_monitor import config
from hardware_monitor.config import COMPUTER_NAME
from hardware_monitor.gemini_client import (
    GEMINI_API_URL,
    GEMINI_MODEL,
    MAX_ATTEMPTS,
    MAX_SUGGESTION_CHARS,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_BACKOFF_SECONDS,
    SuggestionResult,
    build_prompt,
    suggest_fixes,
)

KEY = "secret-gemini-key"


def ask(api_key: str | None = KEY, **kwargs):
    return suggest_fixes(api_key, "Camera Disconnected", "no frame for 3 s", "camera", **kwargs)


def test_the_gemini_defaults():
    # the one place the defaults are pinned; every other test reads them from gemini_client
    assert (GEMINI_MODEL, REQUEST_TIMEOUT_SECONDS, MAX_ATTEMPTS, RETRY_BACKOFF_SECONDS, MAX_SUGGESTION_CHARS) == \
        ("gemini-3.1-flash-lite", 10, 2, 1, 3000)


# --- the key ---

def test_the_key_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", " AIza-example-key\n")
    assert config.get_gemini_api_key() == "AIza-example-key"


@pytest.mark.parametrize("saved", [None, "", "   "], ids=["missing", "empty", "only-spaces"])
def test_no_usable_key_gives_none(monkeypatch, saved):
    if saved is not None:
        monkeypatch.setenv("GEMINI_API_KEY", saved)
    assert config.get_gemini_api_key() is None


@pytest.mark.parametrize("api_key", [None, ""], ids=["no-key", "empty-key"])
def test_without_a_key_nothing_is_sent(gemini, api_key):
    result = ask(api_key)
    assert gemini.calls == []
    assert not result.ok and result.attempts == 0 and "key" in result.error


# --- the request ---

def test_success_returns_the_suggestion(gemini):
    assert ask() == SuggestionResult(True, text=SUGGESTION)


def test_success_sends_the_expected_request(gemini):
    ask()
    gemini.post.assert_called_once_with(
        f"{GEMINI_API_URL}/models/{GEMINI_MODEL}:generateContent",
        json={"contents": [{"parts": [{"text": build_prompt("Camera Disconnected", "no frame for 3 s", "camera")}]}]},
        headers={"x-goog-api-key": KEY, "Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


def test_the_key_is_sent_in_a_header_never_in_the_url(gemini):
    # a key in the URL ends up in logs and in requests' error messages
    ask()
    assert KEY not in gemini.calls[0].url


def test_the_prompt_describes_the_fault():
    prompt = build_prompt("Microphone Disconnected", "no input device found", "mic_conn")
    assert all(part in prompt for part in ("Microphone Disconnected", "no input device found", "mic_conn"))


# --- the reply ---

def test_a_reply_in_several_parts_is_joined(gemini):
    gemini.reply = answer("1. Check the cable", ".\n2. Restart the app.")
    assert ask().text == "1. Check the cable.\n2. Restart the app."


def test_surrounding_whitespace_is_trimmed(gemini):
    gemini.reply = answer("\n\n  1. Check the cable.  \n")
    assert ask().text == "1. Check the cable."


def test_a_very_long_reply_is_cut_to_the_limit(gemini):
    # the suggestion goes into a GitHub issue: a runaway reply can't flood it
    gemini.reply = answer("Check the cable. " * 1000)
    text = ask().text
    assert len(text) <= MAX_SUGGESTION_CHARS and text.startswith("Check the cable.")


@pytest.mark.parametrize("payload, problem", [
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "SAFETY"),
    ({"candidates": []}, "no suggestion"),
    ({"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}, "no suggestion"),
    ({"candidates": [{"content": {"parts": [{"text": "   "}]}}]}, "no suggestion"),
    ({"candidates": [{"finishReason": "SAFETY"}]}, "no suggestion"),
    ({"candidates": "not a list"}, "no suggestion"),
], ids=["blocked-prompt", "no-candidates", "no-parts", "blank-text", "no-content", "candidates-not-a-list"])
def test_replies_without_a_usable_suggestion_are_rejected(gemini, payload, problem):
    gemini.reply = api_response(200, payload)
    result = ask()
    assert not result.ok and result.text is None and problem in result.error


@pytest.mark.parametrize("payload, error", [
    (NOT_JSON, "not valid JSON"),
    (["a", "list"], "not a JSON object"),
], ids=["not-json", "json-list"])
def test_replies_that_are_not_a_json_object_are_rejected(gemini, payload, error):
    gemini.reply = api_response(200, payload)
    result = ask()
    assert not result.ok and error in result.error


# --- errors ---

@pytest.mark.parametrize("status, message", [
    (400, "API key not valid. Please pass a valid API key."),
    (403, "Permission denied."),
    (404, "models/unknown is not found."),
    (429, "Resource has been exhausted (e.g. check quota)."),
    (500, "Internal error."),
], ids=["bad-key", "forbidden", "unknown-model", "over-quota", "server-error"])
def test_http_errors_are_reported_with_gemini_s_message(gemini, status, message):
    gemini.reply = api_error(status, message)
    result = ask(max_attempts=1)
    assert not result.ok and f"HTTP {status}" in result.error and message in result.error


def test_an_error_reply_that_is_not_json_still_gives_the_status(gemini):
    gemini.reply = api_response(502, NOT_JSON, text="<html>Bad Gateway</html>")
    assert "HTTP 502" in ask(max_attempts=1).error


def test_a_timeout_is_reported(gemini):
    gemini.reply = requests.exceptions.ReadTimeout()
    result = ask(max_attempts=1)
    assert not result.ok and f"did not answer within {REQUEST_TIMEOUT_SECONDS} s" in result.error


def test_a_connection_error_is_reported_not_raised(gemini):
    gemini.reply = requests.exceptions.ConnectionError("Name or service not known")
    result = ask(max_attempts=1)
    assert not result.ok and "Could not connect to Gemini" in result.error


def test_the_key_never_appears_in_an_error(gemini, capsys):
    # requests quotes the URL and headers in some messages
    gemini.reply = requests.exceptions.ConnectionError(f"failed with header x-goog-api-key: {KEY}")
    result = ask()
    assert KEY not in result.error and KEY not in capsys.readouterr().out


# --- retries ---

@pytest.mark.parametrize("temporary_failure", [
    requests.exceptions.ReadTimeout(),
    requests.exceptions.ConnectionError(),
    api_error(429, "Resource has been exhausted."),
    api_error(503, "The model is overloaded."),
], ids=["timeout", "connection-error", "over-quota", "overloaded"])
def test_recovers_after_a_temporary_failure(gemini, retry_waits, temporary_failure):
    gemini.reply = [temporary_failure, answer(SUGGESTION)]
    result = ask()
    assert result == SuggestionResult(True, text=SUGGESTION, attempts=2)
    assert retry_waits == [RETRY_BACKOFF_SECONDS]


def test_gives_up_after_the_maximum_attempts(gemini, retry_waits):
    gemini.reply = api_error(503, "The model is overloaded.")
    result = ask()
    assert len(gemini.calls) == MAX_ATTEMPTS and result.attempts == MAX_ATTEMPTS
    assert result.error.startswith(f"Gave up after {MAX_ATTEMPTS} attempt(s).")


@pytest.mark.parametrize("status", [400, 403, 404], ids=["bad-key", "forbidden", "unknown-model"])
def test_permanent_errors_stop_immediately(gemini, retry_waits, status):
    gemini.reply = api_error(status, "no")
    assert ask().attempts == 1 and len(gemini.calls) == 1 and retry_waits == []


def test_a_reply_without_a_suggestion_is_not_retried(gemini, retry_waits):
    # a blocked prompt would be blocked again
    gemini.reply = api_response(200, {"promptFeedback": {"blockReason": "SAFETY"}})
    assert ask().attempts == 1 and retry_waits == []


# --- the dashboard's GitHub issues (the shared `dash` fixture) ---

@pytest.fixture
def ai_dash(dash, gemini):
    """The shared dashboard, with a Gemini key; .gemini is the mocked Gemini."""
    dash.app.gemini_api_key = KEY
    dash.gemini = gemini
    return dash


def send_camera_alert(dash, request=None):
    dash.app.send_github_alert("camera", "Camera Disconnected", "no frame", request)
    return dash.github.posted[-1].body


def test_the_dashboard_reads_the_key_from_the_environment(dash, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    assert dash.build().gemini_api_key == KEY


def test_the_issue_includes_the_suggestion_after_the_alert(ai_dash):
    body = send_camera_alert(ai_dash)
    assert SUGGESTION in body
    before_it = body.partition(SUGGESTION)[0]  # the alert, then a heading saying where the text came from
    assert "Hardware Alert Triggered" in before_it and "AI-generated" in before_it and "Gemini" in before_it
    assert ai_dash.gemini.prompts == [build_prompt("Camera Disconnected", "no frame", "camera")]


def test_nothing_identifying_a_person_is_sent_to_gemini(ai_dash, alerts):
    desk = ai_dash.app.maintenance_desk
    request = desk.open_request_for_alert(alerts.make(component_id="CAM-LOCAL", workstation_id=COMPUTER_NAME))
    technician = desk.technician_for(request)

    body = send_camera_alert(ai_dash, request)

    assert technician.name in body                    # the issue still names them
    (prompt,) = ai_dash.gemini.prompts
    assert COMPUTER_NAME not in prompt and technician.name not in prompt


@pytest.mark.parametrize("failure, reason", [
    (api_error(400, "API key not valid."), "HTTP 400"),
    (requests.exceptions.ReadTimeout(), "did not answer"),
    (api_response(200, {"promptFeedback": {"blockReason": "SAFETY"}}), "SAFETY"),
], ids=["bad-key", "timeout", "blocked"])
def test_when_gemini_fails_the_issue_is_still_posted(ai_dash, capsys, failure, reason):
    ai_dash.gemini.reply = failure
    body = send_camera_alert(ai_dash)

    assert "Hardware Alert Triggered" in body and "AI-generated" not in body
    output = capsys.readouterr().out
    assert "Alert posted to GitHub for camera" in output
    assert "Gemini" in output and reason in output and KEY not in output


def test_without_a_key_gemini_is_not_asked(dash, gemini):
    body = send_camera_alert(dash)
    assert gemini.calls == [] and "AI-generated" not in body


def test_the_key_never_reaches_github(ai_dash):
    assert KEY not in send_camera_alert(ai_dash)


def test_gemini_is_asked_once_per_issue_not_once_per_check(ai_dash):
    app = ai_dash.app
    for _ in range(3):  # the same fault seen on three checks: one call, one issue
        app.show_alert("camera", "Camera Disconnected", "test")
        app.root.flush()
    assert len(ai_dash.github.posted) == 1 and len(ai_dash.gemini.calls) == 1
