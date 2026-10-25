"""The GitHub API, faked at its external dependency: requests.post. One
mocked post for every test that sends an alert (see the `github` fixture in
conftest.py); the code that builds the request and reads the reply runs
for real."""

from types import SimpleNamespace
from unittest import mock

import requests

NOT_JSON = object()
CREATED = {"number": 42, "html_url": "https://github.com/owner/repo/issues/42", "title": "Camera down"}


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


def created():
    """GitHub's reply to a new issue: 201 with issue #42."""
    return api_response(201, CREATED)


class MockedGitHub:
    """Wraps the mocked requests.post (autospec, so a call the real one would
    reject fails the test).

    .reply: None (the default) creates every issue, numbered 1, 2, 3...;
    a response or exception is used for every attempt; a list gives one per
    attempt. .calls and .posted show what was sent."""

    def __init__(self, post):
        self.post, self.reply = post, None
        post.side_effect = self._answer

    def _answer(self, url, **kwargs):
        if self.reply is None:
            number = self.post.call_count
            return api_response(201, {"number": number, "html_url": f"https://example.invalid/{number}"})
        reply = self.reply.pop(0) if isinstance(self.reply, list) else self.reply
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def calls(self):
        """Every request, as SimpleNamespace(url, json, headers, timeout)."""
        return [SimpleNamespace(url=c.args[0], **c.kwargs) for c in self.post.call_args_list]

    @property
    def posted(self):
        """Every issue sent: its alert key, title, body and the token used."""
        return [SimpleNamespace(key=c.json["labels"][1], title=c.json["title"], body=c.json["body"],
                                token=c.headers["Authorization"]) for c in self.calls]
