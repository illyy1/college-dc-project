"""What the faked web APIs share (fake_github.py, fake_gemini.py): replies
shaped like a real requests.Response, and a wrapper around a mocked post
that answers with scripted replies and records what was sent."""

from types import SimpleNamespace
from unittest import mock

import requests

NOT_JSON = object()


def api_response(status_code, payload: object = NOT_JSON, text=""):
    """A mock with only the attributes a real requests.Response has."""
    response = mock.Mock(spec=requests.Response)
    response.status_code = status_code
    response.text = text
    if payload is NOT_JSON:
        response.json.side_effect = requests.exceptions.JSONDecodeError("Expecting value", text, 0)
    else:
        response.json.return_value = payload
    return response


class MockedPost:
    """Wraps a mocked post (autospec, so a call the real one would reject
    fails the test).

    .reply: None (the default) answers with default_reply(); a response or
    exception is used for every attempt; a list gives one per attempt.
    .calls shows what was sent."""

    def __init__(self, post):
        self.post, self.reply = post, None
        post.side_effect = self._answer

    def default_reply(self):
        """The service's normal answer; each fake defines its own."""
        raise NotImplementedError

    def _answer(self, url, **kwargs):
        if self.reply is None:
            return self.default_reply()
        reply = self.reply.pop(0) if isinstance(self.reply, list) else self.reply
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def calls(self):
        """Every request, as SimpleNamespace(url, json, headers, timeout)."""
        return [SimpleNamespace(url=c.args[0], **c.kwargs) for c in self.post.call_args_list]
