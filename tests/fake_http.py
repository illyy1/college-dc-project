"""What the faked web APIs share (fake_github.py, fake_gemini.py): real
requests.Response replies, and a wrapper around a mocked post
that answers with scripted replies and records what was sent."""

import json
from http import HTTPStatus
from types import SimpleNamespace

import requests

NOT_JSON = object()


def api_response(status_code, payload: object = NOT_JSON, text=""):
    """A real requests.Response, as a server would send it: the body is
    `payload` as JSON, or `text` (e.g. an HTML error page) when payload is
    NOT_JSON. Its raise_for_status() and json() are requests' own."""
    response = requests.Response()
    response.status_code = status_code
    response.reason = HTTPStatus(status_code).phrase
    response.url = "https://api.example.invalid/"
    response.encoding = "utf-8"
    body = text if payload is NOT_JSON else json.dumps(payload)
    response._content = body.encode("utf-8")  # what requests sets once the body has been read
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
