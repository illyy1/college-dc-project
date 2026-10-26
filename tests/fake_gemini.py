"""The Gemini API, faked at its external dependency: the `post` that
gemini_client imports from requests (see the `gemini` fixture in
conftest.py). Patched apart from GitHub's requests.post, so a test can fake
both services at once; the code that builds the request and reads the
reply runs for real."""

from fake_http import MockedPost, api_response

SUGGESTION = "1. Check that the USB cable is plugged in.\n2. Close other apps that use the camera."


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


class MockedGemini(MockedPost):
    """The mocked post (see fake_http.MockedPost). By default every request
    is answered with SUGGESTION; .prompts shows the prompts sent."""

    def default_reply(self):
        return answer(SUGGESTION)

    @property
    def prompts(self):
        """The prompt text of every request."""
        return [call.json["contents"][0]["parts"][0]["text"] for call in self.calls]
