"""GitHub Issues client the dashboard uses to report hardware faults. Kept
apart from the Tkinter code so the API handling (success, HTTP errors,
timeouts, malformed replies) can be tested without a window or a network."""

from dataclasses import dataclass

import requests

GITHUB_API_URL = "https://api.github.com"
REQUEST_TIMEOUT_SECONDS = 5
# fields of GitHub's "issue created" reply the dashboard relies on, with their types
REQUIRED_RESPONSE_FIELDS = {"number": int, "html_url": str}


@dataclass
class IssueResult:
    ok: bool
    issue_number: int | None = None
    url: str | None = None
    error: str | None = None


def create_issue(token, repo, title, body, labels=(), timeout=REQUEST_TIMEOUT_SECONDS):
    """Opens a GitHub issue. Never raises: a missing token, network error,
    timeout, HTTP error or malformed reply all come back as
    IssueResult(ok=False, error=...), so a failed alert can't crash the
    monitoring thread that sent it."""
    if not token:
        return IssueResult(False, error="GitHub token missing or decryption failed.")

    try:
        response = requests.post(
            f"{GITHUB_API_URL}/repos/{repo}/issues",
            json={"title": title, "body": body, "labels": list(labels)},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"},
            timeout=timeout,
        )
    except requests.exceptions.Timeout:
        return IssueResult(False, error=f"GitHub did not answer within {timeout} s.")
    except requests.exceptions.RequestException as e:
        return IssueResult(False, error=f"Network error: {e}")

    if response.status_code != 201:
        return IssueResult(False, error=f"GitHub returned HTTP {response.status_code}{_api_message(response)}.")
    return _parse_created_issue(response)


def _api_message(response):
    """GitHub explains errors in a JSON "message" field; returns ': <message>' or ''."""
    try:
        message = response.json().get("message")
    except (ValueError, AttributeError):  # not JSON, or JSON that is not an object
        return ""
    return f": {message}" if message else ""


def _parse_created_issue(response):
    try:
        data = response.json()
    except ValueError:
        return IssueResult(False, error="GitHub's reply was not valid JSON.")
    if not isinstance(data, dict):
        return IssueResult(False, error="GitHub's reply was not a JSON object.")

    problems = []
    for field, expected in REQUIRED_RESPONSE_FIELDS.items():
        value = data.get(field)
        if value is None:
            problems.append(f"missing '{field}'")
        elif isinstance(value, bool) or not isinstance(value, expected):
            problems.append(f"'{field}' should be {expected.__name__}, got {value!r}")
    if problems:
        return IssueResult(False, error=f"GitHub's reply is incomplete: {', '.join(problems)}.")

    return IssueResult(True, issue_number=data["number"], url=data["html_url"])
