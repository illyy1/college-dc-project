"""The Gemini API, faked at its external dependency: the `post` that
api_client imports from requests for Gemini (see the `gemini` fixture in
conftest.py). Patched apart from GitHub's requests.post, so a test can fake
both services at once; the code that builds the request and reads the
reply runs for real."""

import json

from fake_http import MockedPost, api_response

# Gemini's triage of a fault, as the JSON it is asked for (api_client.TRIAGE_SCHEMA)
TRIAGE = {"priority": 2, "cause": "cable", "repair_minutes": 15,
          "steps": ["Reseat the USB cable.", "Try another USB port."]}


def answer(*texts, finish="STOP"):
    """Gemini's 200 reply: one candidate whose text comes in these parts, and
    why the model stopped (STOP: it finished; None leaves the field out)."""
    candidate = {"content": {"role": "model", "parts": [{"text": text} for text in texts]}}
    if finish is not None:
        candidate["finishReason"] = finish
    return api_response(200, {"candidates": [candidate]})


def api_error(status, message):
    """Gemini's error reply, e.g. 400 for a bad key or 429 when over the quota."""
    return api_response(status, {"error": {"code": status, "message": message}})


def triage_answer(finish="STOP", **changes):
    """Gemini's 200 reply carrying TRIAGE as JSON text, with any field
    changed, or left out when set to None."""
    triage = {**TRIAGE, **changes}
    return answer(json.dumps({key: value for key, value in triage.items() if value is not None}), finish=finish)


class MockedGemini(MockedPost):
    """The mocked post (see fake_http.MockedPost). By default every request
    is answered with TRIAGE; .prompts shows the prompts sent."""

    def default_reply(self):
        return triage_answer()

    @property
    def prompts(self):
        """The prompt text of every request."""
        return [call.json["contents"][0]["parts"][0]["text"] for call in self.calls]
