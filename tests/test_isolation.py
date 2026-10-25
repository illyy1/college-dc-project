"""Proves the isolation conftest.py gives every test: no internet, no real
API key, and no real waiting between retries. Also proves the mocks sit on
the external dependencies only: the project functions under test are the
real ones."""

import inspect
import os
import socket
import time
from unittest import mock

import pytest
import requests

from conftest import NetworkAccessBlocked
from hardware_monitor import config, github_client
from hardware_monitor.github_client import create_issue


# --- no internet ---

def test_a_raw_connection_is_blocked():
    with pytest.raises(NetworkAccessBlocked, match="example.com"):
        socket.create_connection(("example.com", 80), timeout=1)


def test_a_real_http_request_fails_as_if_offline():
    with pytest.raises(requests.exceptions.ConnectionError, match="test tried to look up 'api.github.com'"):
        requests.get("https://api.github.com", timeout=1)


def test_the_github_client_cannot_reach_github_unmocked():
    # create_issue reports network problems as a result, naming only the exception,
    # so the lookup is watched to show it was the block that stopped it
    with mock.patch("socket.getaddrinfo", wraps=socket.getaddrinfo) as lookup:
        result = create_issue("token", "owner/repo", "title", "body", max_attempts=1)
    assert not result.ok and result.error.endswith("Could not connect to GitHub (ConnectionError).")
    assert lookup.call_args.args[0] == "api.github.com"


# --- no real API key ---

def test_no_token_is_available_from_the_environment_or_the_real_env_file():
    assert "GITHUB_TOKEN" not in os.environ and "ENCRYPTION_KEY" not in os.environ
    assert config.get_github_token() is None  # even though the project's .env may hold a real one


# --- no real waiting between retries ---

def test_the_retry_wait_is_a_mock_not_the_real_sleep():
    assert isinstance(github_client.sleep, mock.Mock)
    assert github_client.sleep is not time.sleep


@pytest.fixture
def server_down(github):
    """GitHub unreachable: the shared `github` mock fails every attempt."""
    github.reply = requests.exceptions.ConnectionError("down")
    return github.post


def test_exhausting_every_retry_takes_no_real_time(server_down, retry_waits):
    started = time.perf_counter()
    result = create_issue("token", "owner/repo", "title", "body", max_attempts=6)
    elapsed = time.perf_counter() - started

    assert retry_waits == [github_client.RETRY_BACKOFF_SECONDS * 2 ** n for n in range(5)]  # 31 s if real, by default
    assert not result.ok and server_down.call_count == 6
    assert elapsed < 0.5


# --- the mocks replace dependencies, never the code under test ---

def test_create_issue_under_test_is_the_real_function(server_down):
    assert not isinstance(create_issue, mock.Mock)
    assert inspect.getsourcefile(create_issue) == inspect.getsourcefile(github_client)
    create_issue("token", "owner/repo", "title", "body", max_attempts=1)
    assert server_down.called  # the real create_issue ran and reached the mocked dependency


def test_the_requests_mock_rejects_calls_the_real_function_would_reject(server_down):
    # autospec copies requests.post's signature, so a wrong call can't slip through
    with pytest.raises(TypeError):
        requests.post()
