import os
import socket
import sys
import types
from unittest import mock

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# The package __init__ imports the Tkinter dashboard. Some environments (CI
# images, containers) ship Python without Tk, so stub it; the tests never
# open a window.
try:
    import tkinter  # noqa: F401
except ImportError:
    sys.modules["tkinter"] = types.ModuleType("tkinter")

from hardware_monitor.repository import (  # noqa: E402
    load_workstations_from_jsonl,
    load_technicians_from_jsonl,
)

SAMPLE_DATA_PATH = os.path.join(PROJECT_ROOT, "data", "sample_data.jsonl")
TECHNICIANS_PATH = os.path.join(PROJECT_ROOT, "data", "technicians.jsonl")


@pytest.fixture
def workstations():
    return load_workstations_from_jsonl(SAMPLE_DATA_PATH)


@pytest.fixture
def technicians():
    return load_technicians_from_jsonl(TECHNICIANS_PATH)


@pytest.fixture(autouse=True)
def isolated_alert_log(tmp_path, monkeypatch):
    """Keeps AlertManager from appending to the real logs/ folder."""
    monkeypatch.setattr("hardware_monitor.processing.LOG_PATH", str(tmp_path / "hardware_history.jsonl"))


# --- isolation from external dependencies, applied to every test ---

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect


class NetworkAccessBlocked(socket.gaierror):
    """The error an offline machine gives when a name can't be resolved, so
    the code under test sees a realistic network failure."""


@pytest.fixture(autouse=True)
def no_internet():
    """Any attempt to reach a non-local address fails at once, so a test can
    never depend on the internet. Loopback stays allowed: asyncio on Windows
    connects a local socket pair internally."""

    def getaddrinfo(host, *args, **kwargs):
        if host not in LOCAL_HOSTS:
            raise NetworkAccessBlocked(f"test tried to look up {host!r}")
        return _real_getaddrinfo(host, *args, **kwargs)

    def connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in LOCAL_HOSTS:
            raise NetworkAccessBlocked(f"test tried to connect to {address!r}")
        return _real_connect(sock, address)

    with mock.patch.object(socket, "getaddrinfo", getaddrinfo), \
            mock.patch.object(socket.socket, "connect", connect):
        yield


@pytest.fixture(autouse=True)
def no_real_secrets(monkeypatch):
    """No test can read the real .env or a token from the environment;
    tests that need a token set their own (encrypted with a throwaway key)."""
    try:
        import dotenv
    except ImportError:  # the demo-only install has no python-dotenv
        pass
    else:
        monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)


@pytest.fixture(autouse=True)
def retry_waits():
    """Replaces the sleep between GitHub retries with a mock, so retries cost
    no time. Yields the list of waits that would have happened."""
    waits = []
    with mock.patch("hardware_monitor.github_client.sleep", side_effect=waits.append):
        yield waits
