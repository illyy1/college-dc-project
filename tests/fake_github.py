"""The GitHub API, faked at its external dependencies: requests.post (new
issues) and requests.get (the open issues). One mocked pair for every test
that sends an alert (see the `github` fixture in conftest.py); the code
that builds the request and reads the reply runs for real."""

from types import SimpleNamespace

from fake_http import MockedPost, api_response

CREATED = {"number": 42, "html_url": "https://github.com/owner/repo/issues/42", "title": "Camera down"}


def created():
    """GitHub's reply to a new issue: 201 with issue #42."""
    return api_response(201, CREATED)


class MockedGitHub(MockedPost):
    """The mocked requests.post (see fake_http.MockedPost). By default every
    issue is created, numbered 1, 2, 3...; .posted shows the issues sent."""

    def default_reply(self):
        number = self.post.call_count
        return api_response(201, {"number": number, "html_url": f"https://github.com/owner/repo/issues/{number}"})

    @property
    def posted(self):
        """Every issue sent: its alert key, title, body and the token used."""
        return [SimpleNamespace(key=c.json["labels"][1], title=c.json["title"], body=c.json["body"],
                                token=c.headers["Authorization"]) for c in self.calls]


def open_issue(number, title, url=None):
    """One open issue, as GitHub lists it."""
    return {"number": number, "title": title, "state": "open",
            "html_url": url or f"https://github.com/owner/repo/issues/{number}"}


def open_issues(*issues):
    """GitHub's 200 reply listing these open issues."""
    return api_response(200, list(issues))


class MockedGitHubSearch(MockedPost):
    """The mocked requests.get (see fake_http.MockedPost; it fakes any
    request function). By default no issue is open."""

    def default_reply(self):
        return open_issues()
