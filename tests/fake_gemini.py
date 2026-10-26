"""The Gemini API, faked at its external dependency: the `post` that
gemini_client imports from requests (see the `gemini` fixture in
conftest.py). Patched apart from GitHub's requests.post, so a test can fake
both services at once; the code that builds the request and reads the
reply runs for real."""

from types import SimpleNamespace

from fake_github import api_response

SUGGESTION = "1. Check that the USB cable is plugged in.\n2. Close other apps that use the camera."


def answer(*texts):
    """Gemini's 200 reply: one candidate whose text comes in these parts."""
    return api_response(200, {"candidates": [{
        "content": {"role": "model", "parts": [{"text": text} for text in texts]},
        "finishReason": "STOP",
    }]})


def api_error(status, message):
    """Gemini's error reply, e.g. 400 for a bad key or 429 when over the quota."""
    return api_response(status, {"error": {"code": status, "message": message}})


class MockedGemini:
    """Wraps the mocked post (autospec, so a call the real one would reject
    fails the test).

    .reply: None (the default) answers every request with SUGGESTION; a
    response or exception is used for every attempt; a list gives one per
    attempt. .calls and .prompts show what was sent."""

    def __init__(self, post):
        self.post, self.reply = post, None
        post.side_effect = self._answer

    def _answer(self, url, **kwargs):
        if self.reply is None:
            return answer(SUGGESTION)
        reply = self.reply.pop(0) if isinstance(self.reply, list) else self.reply
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def calls(self):
        """Every request, as SimpleNamespace(url, json, headers, timeout)."""
        return [SimpleNamespace(url=c.args[0], **c.kwargs) for c in self.post.call_args_list]

    @property
    def prompts(self):
        """The prompt text of every request."""
        return [call.json["contents"][0]["parts"][0]["text"] for call in self.calls]
