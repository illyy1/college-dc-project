"""TDD specification for the Gemini part of hardware_monitor/api_client.py
(the GEMINI_ constants keep their short names here): Gemini triages
each hardware fault, and the triage drives the system (business application):
it can raise the call's priority, labels the GitHub issue, and feeds the
maintenance report (tests/test_business_application.py).

What the module must provide:
- triage_fault(api_key, title, details, component, timeout=..., max_attempts=...)
    -> TriageResult(ok, triage=None, error=None, attempts=1)
    One POST to {GEMINI_API_URL}/models/{GEMINI_MODEL}:generateContent with
    the prompt from build_prompt() and generationConfig asking for JSON in
    TRIAGE_SCHEMA; the key goes in the x-goog-api-key header, never the URL.
    Temporary failures (timeouts, connection errors, HTTP 429/5xx) are
    retried through retry(). Never raises for API problems.
- Triage(priority, cause, repair_minutes, steps): priority 1 (most urgent)
    to 5, like the system's own; cause one of CAUSES; repair_minutes 1 to
    MAX_REPAIR_MINUTES; steps a tuple of 1 to MAX_STEPS non-blank texts,
    each cut to MAX_STEP_CHARS. Anything else is rejected, not guessed.
- build_prompt(title, details, component) -> str
- constants GEMINI_API_URL, GEMINI_MODEL, REQUEST_TIMEOUT_SECONDS,
  MAX_ATTEMPTS, RETRY_BACKOFF_SECONDS, CAUSES, MAX_STEPS, MAX_STEP_CHARS,
  MAX_REPAIR_MINUTES, TRIAGE_SCHEMA

And hardware_monitor.config.get_gemini_api_key(): GEMINI_API_KEY from .env
(plain text), or None.

The dashboard: when it has a key, each GitHub issue gets the triage under a
heading that says it is AI-generated. Without a key, or when Gemini fails,
the issue is posted as before. Nothing that identifies a person (the
computer's name, the technician) is sent to Gemini.

Only the external dependencies are mocked: Gemini's post (the `gemini`
fixture, fake_gemini.py), GitHub's requests.post (`github`) and the sleep
between retries. triage_fault and the dashboard run for real."""

import json

import pytest
import requests

from fake_gemini import TRIAGE, answer, api_error, triage_answer
from fake_http import NOT_JSON, api_response, watching_raise_for_status
from hardware_monitor import config
from hardware_monitor.config import COMPUTER_NAME

from conftest import GEMINI_KEY
from hardware_monitor.api_client import (
    CAUSES,
    GEMINI_API_URL,
    GEMINI_MAX_ATTEMPTS as MAX_ATTEMPTS,
    GEMINI_MODEL,
    GEMINI_RETRY_BACKOFF_SECONDS as RETRY_BACKOFF_SECONDS,
    GEMINI_TIMEOUT_SECONDS as REQUEST_TIMEOUT_SECONDS,
    MAX_REPAIR_MINUTES,
    MAX_STEP_CHARS,
    MAX_STEPS,
    TRIAGE_SCHEMA,
    Triage,
    TriageResult,
    build_prompt,
    triage_fault,
)

KEY = GEMINI_KEY  # the key ai_dash uses
# TRIAGE (fake_gemini) as the object triage_fault must return
EXPECTED = Triage(**{**TRIAGE, "steps": tuple(TRIAGE["steps"])})


def ask(api_key: str | None = KEY, **kwargs):
    return triage_fault(api_key, "Camera Disconnected", "no frame for 3 s", "camera", **kwargs)


def test_the_gemini_defaults():
    # the one place the defaults are pinned; every other test reads them from api_client
    assert (GEMINI_MODEL, REQUEST_TIMEOUT_SECONDS, MAX_ATTEMPTS, RETRY_BACKOFF_SECONDS) == \
        ("gemini-3.1-flash-lite", 10, 2, 1)
    assert (MAX_STEPS, MAX_STEP_CHARS, MAX_REPAIR_MINUTES) == (5, 300, 480)
    assert CAUSES == ("cable", "driver", "hardware", "permissions", "software", "other")


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

def test_success_returns_the_triage(gemini):
    assert ask() == TriageResult(True, triage=EXPECTED)


def test_success_sends_the_expected_request(gemini):
    ask()
    gemini.post.assert_called_once_with(
        f"{GEMINI_API_URL}/models/{GEMINI_MODEL}:generateContent",
        json={
            "contents": [{"parts": [{"text": build_prompt("Camera Disconnected", "no frame for 3 s", "camera")}]}],
            # Gemini's JSON mode: the answer must follow the schema
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": TRIAGE_SCHEMA},
        },
        headers={"x-goog-api-key": KEY, "Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


def test_the_schema_asks_for_every_triage_field():
    assert set(TRIAGE_SCHEMA["required"]) == {"priority", "cause", "repair_minutes", "steps"}
    assert TRIAGE_SCHEMA["properties"]["cause"]["enum"] == list(CAUSES)


def test_the_key_is_sent_in_a_header_never_in_the_url(gemini):
    # a key in the URL ends up in logs and in requests' error messages
    ask()
    assert KEY not in gemini.calls[0].url


def test_the_prompt_describes_the_fault():
    prompt = build_prompt("Microphone Disconnected", "no input device found", "mic_conn")
    assert all(part in prompt for part in ("Microphone Disconnected", "no input device found", "mic_conn"))


# --- the reply: the triage ---

def test_an_answer_in_several_parts_is_joined_before_reading(gemini):
    text = json.dumps(TRIAGE)
    gemini.reply = answer(text[:20], text[20:])
    assert ask().triage == EXPECTED


def test_whitespace_around_the_answer_and_its_steps_is_trimmed(gemini):
    gemini.reply = answer("\n  " + json.dumps({**TRIAGE, "steps": ["  Reseat the USB cable.  "]}) + "\n")
    assert ask().triage.steps == ("Reseat the USB cable.",)


def test_only_the_first_steps_are_kept(gemini):
    gemini.reply = triage_answer(steps=[f"Step {n}." for n in range(1, 9)])
    assert ask().triage.steps == tuple(f"Step {n}." for n in range(1, MAX_STEPS + 1))


def test_a_very_long_step_is_cut(gemini):
    # the steps go into a GitHub issue: a runaway answer can't flood it
    gemini.reply = triage_answer(steps=["Check the cable. " * 100])
    (step,) = ask().triage.steps
    assert len(step) <= MAX_STEP_CHARS and step.startswith("Check the cable.")


@pytest.mark.parametrize("cause", CAUSES)
def test_every_known_cause_is_accepted(gemini, cause):
    gemini.reply = triage_answer(cause=cause)
    assert ask().triage.cause == cause


@pytest.mark.parametrize("changes, problem", [
    ({"priority": None}, "missing 'priority'"),
    ({"priority": 0}, "'priority' should be 1 to 5, got 0"),
    ({"priority": 6}, "'priority' should be 1 to 5, got 6"),
    ({"priority": "2"}, "'priority' should be 1 to 5, got '2'"),
    ({"priority": True}, "'priority' should be 1 to 5, got True"),
    ({"cause": None}, "missing 'cause'"),
    ({"cause": "magic"}, "'cause' should be one of"),
    ({"cause": 3}, "'cause' should be one of"),
    ({"repair_minutes": None}, "missing 'repair_minutes'"),
    ({"repair_minutes": 0}, f"'repair_minutes' should be 1 to {MAX_REPAIR_MINUTES}, got 0"),
    ({"repair_minutes": MAX_REPAIR_MINUTES + 1}, f"'repair_minutes' should be 1 to {MAX_REPAIR_MINUTES}"),
    ({"repair_minutes": "15"}, "'repair_minutes' should be 1 to"),
    ({"steps": None}, "missing 'steps'"),
    ({"steps": []}, "'steps' should be a list of texts"),
    ({"steps": "Reseat the cable."}, "'steps' should be a list of texts"),
    ({"steps": ["Reseat.", 3]}, "'steps' should be a list of texts"),
    ({"steps": ["Reseat.", "   "]}, "'steps' should be a list of texts"),
], ids=["no-priority", "priority-zero", "priority-six", "priority-as-text", "priority-as-bool",
        "no-cause", "unknown-cause", "cause-a-number", "no-minutes", "minutes-zero", "minutes-too-many",
        "minutes-as-text", "no-steps", "steps-empty", "steps-a-text", "step-a-number", "step-blank"])
def test_a_triage_with_a_missing_or_impossible_value_is_rejected(gemini, changes, problem):
    gemini.reply = triage_answer(**changes)
    result = ask()
    assert not result.ok and result.triage is None
    assert result.error.startswith("Gemini's triage is invalid: ") and problem in result.error


@pytest.mark.parametrize("text, error", [
    ("1. Reseat the USB cable.", "Gemini's answer was not valid JSON."),
    ('["cable", 2]', "Gemini's answer was not a JSON object."),
], ids=["plain-text", "json-list"])
def test_an_answer_that_is_not_a_json_object_is_rejected(gemini, text, error):
    gemini.reply = answer(text)
    result = ask()
    assert (result.ok, result.error) == (False, error)  # its category: test_error_categories.py


@pytest.mark.parametrize("payload, problem", [
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "SAFETY"),
    ({"candidates": []}, "no answer"),
    ({"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}, "no answer"),
    ({"candidates": [{"content": {"parts": [{"text": "   "}]}}]}, "no answer"),
    ({"candidates": [{"finishReason": "SAFETY"}]}, "no answer"),
    ({"candidates": "not a list"}, "no answer"),
], ids=["blocked-prompt", "no-candidates", "no-parts", "blank-text", "no-content", "candidates-not-a-list"])
def test_replies_without_an_answer_are_rejected(gemini, payload, problem):
    gemini.reply = api_response(200, payload)
    result = ask()
    assert not result.ok and result.triage is None and problem in result.error


@pytest.mark.parametrize("payload, error", [
    (NOT_JSON, "not valid JSON"),
    (["a", "list"], "not a JSON object"),
], ids=["not-json", "json-list"])
def test_replies_that_are_not_a_json_object_are_rejected(gemini, payload, error):
    gemini.reply = api_response(200, payload)
    result = ask()
    assert not result.ok and error in result.error


# --- response validation: the status, then the JSON, then every field's value ---

def test_the_http_status_is_checked_with_raise_for_status(gemini):
    with watching_raise_for_status() as check:
        ask()
    check.assert_called_once()


def test_an_answer_that_finished_is_used(gemini):
    gemini.reply = triage_answer(finish="STOP")
    assert ask() == TriageResult(True, triage=EXPECTED)


@pytest.mark.parametrize("finish", ["MAX_TOKENS", "SAFETY", "RECITATION", "PROHIBITED_CONTENT", "OTHER"])
def test_an_answer_stopped_for_any_other_reason_is_rejected(gemini, finish):
    # cut off (MAX_TOKENS) or stopped for safety: the JSON may be half-written
    gemini.reply = triage_answer(finish=finish)
    result = ask()
    assert not result.ok and result.triage is None
    assert result.error == f"Gemini stopped before finishing the triage ({finish})."


def test_an_answer_without_a_finish_reason_is_rejected(gemini):
    gemini.reply = triage_answer(finish=None)
    result = ask()
    assert not result.ok and result.error == "Gemini's reply is incomplete: missing 'finishReason'."


@pytest.mark.parametrize("part", [{"text": 5}, {"inlineData": {"mimeType": "image/png"}}, "text"],
                         ids=["text-a-number", "not-text", "part-not-an-object"])
def test_parts_that_are_not_text_are_left_out(gemini, part):
    gemini.reply = api_response(200, {"candidates": [{
        "content": {"role": "model", "parts": [{"text": json.dumps(TRIAGE)}, part]}, "finishReason": "STOP"}]})
    assert ask().triage == EXPECTED


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
    gemini.reply = [temporary_failure, triage_answer()]
    assert ask() == TriageResult(True, triage=EXPECTED, attempts=2)
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


@pytest.mark.parametrize("reply", [
    api_response(200, {"promptFeedback": {"blockReason": "SAFETY"}}),
    triage_answer(priority=9),
], ids=["blocked-prompt", "invalid-triage"])
def test_an_unusable_answer_is_not_retried(gemini, retry_waits, reply):
    # the same prompt would get the same answer
    gemini.reply = reply
    assert ask().attempts == 1 and retry_waits == []


# --- the dashboard's GitHub issues (the shared `dash` and `ai_dash` fixtures) ---


def send_camera_alert(dash, request=None):
    dash.app.send_github_alert("camera", "Camera Disconnected", "no frame", request)
    return dash.github.posted[-1].body


def test_the_dashboard_reads_the_key_from_the_environment(dash, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    assert dash.build().gemini_api_key == KEY


def test_the_issue_shows_the_triage_after_the_alert(ai_dash):
    body = send_camera_alert(ai_dash)

    alert, heading, triage = body.partition("AI-generated by Gemini")
    assert "Hardware Alert Triggered" in alert and heading
    assert all(step in triage for step in TRIAGE["steps"])
    assert "cable" in triage and "15 min" in triage
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
    (triage_answer(cause="magic"), "triage is invalid"),
], ids=["bad-key", "timeout", "blocked", "invalid-triage"])
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
