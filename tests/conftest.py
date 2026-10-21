import hashlib
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

from hardware_monitor.maintenance import MaintenanceDesk  # noqa: E402
from hardware_monitor.models import Camera, Microphone, Workstation  # noqa: E402
from hardware_monitor.processing import Alert, AlertManager  # noqa: E402
from hardware_monitor.repository import (  # noqa: E402
    load_workstations_from_jsonl,
    load_technicians_from_jsonl,
)

SAMPLE_DATA_PATH = os.path.join(PROJECT_ROOT, "data", "sample_data.jsonl")
TECHNICIANS_PATH = os.path.join(PROJECT_ROOT, "data", "technicians.jsonl")


# --- reusable data: the synthetic fleet and technicians from data/ ---

@pytest.fixture
def workstations():
    """The 8 workstations (16 components) from data/sample_data.jsonl, freshly loaded."""
    return load_workstations_from_jsonl(SAMPLE_DATA_PATH)


@pytest.fixture
def technicians():
    """The 4 technicians from data/technicians.jsonl, freshly loaded (no calls assigned yet)."""
    return load_technicians_from_jsonl(TECHNICIANS_PATH)


# --- reusable mock objects ---

class AlertFactory:
    """Builds fresh Alert objects. Each call is a new alert (new alert_id);
    any field can be overridden."""

    def make(self, component_id="CAM-104", workstation_id="WS-104", error_type="CAMERA_FAILURE",
             message="test fault", priority=1, **fields):
        return Alert(component_id, workstation_id, error_type, message, priority=priority, **fields)

    def camera(self, **fields):
        """A lost camera on WS-104 (covered by Camera technicians in technicians.jsonl)."""
        return self.make(**fields)

    def microphone(self, **fields):
        """A silent microphone on WS-105."""
        return self.make(**{"component_id": "MIC-105", "workstation_id": "WS-105",
                            "error_type": "MICROPHONE_SILENCE", "message": "silent", "priority": 4, **fields})


@pytest.fixture
def alerts():
    """alerts.camera(), alerts.microphone() or alerts.make(...) give a fresh Alert."""
    return AlertFactory()


@pytest.fixture
def bench_workstation():
    """A fake workstation with a healthy camera, a camera with an unusable
    resolution and a healthy microphone."""
    bench = Workstation("BENCH", "Repair Bench", "IT room")
    bench.add_component(Camera("CAM-B1", "Bench Cam", "BENCH"))
    bench.add_component(Camera("CAM-B2", "Broken Cam", "BENCH", resolution="0x0"))
    bench.add_component(Microphone("MIC-B1", "Bench Mic", "BENCH"))
    return bench


@pytest.fixture
def alert_manager(tmp_path):
    """An AlertManager whose history log is a temporary file."""
    return AlertManager(log_path=str(tmp_path / "history.jsonl"))


@pytest.fixture
def make_desk(technicians, tmp_path):
    """Builds a MaintenanceDesk on a temporary log. By default it uses the
    technicians fixture and writes no CSV report."""

    def build(technicians=technicians, csv_report=False, **options):
        logs = tmp_path / "logs"
        return MaintenanceDesk(technicians, log_path=str(logs / "maintenance_requests.jsonl"),
                               report_csv_path=str(logs / "maintenance_report.csv") if csv_report else None,
                               **options)

    return build


@pytest.fixture
def desk(make_desk):
    """A MaintenanceDesk with the four technicians, a temporary log and no CSV report."""
    return make_desk()


@pytest.fixture(autouse=True)
def isolated_alert_log(tmp_path, monkeypatch):
    """Keeps AlertManager from appending to the real logs/ folder."""
    monkeypatch.setattr("hardware_monitor.processing.LOG_PATH", str(tmp_path / "hardware_history.jsonl"))


# folders a test run itself may create or change (caches, the venv, git)
UNWATCHED = {".git", ".venv", "__pycache__", ".pytest_cache"}


def project_files():
    """Content hash of every file in the project, outside UNWATCHED folders."""
    hashes = {}
    for folder, subfolders, files in os.walk(PROJECT_ROOT):
        subfolders[:] = [name for name in subfolders if name not in UNWATCHED]
        for name in files:
            path = os.path.join(folder, name)
            with open(path, "rb") as f:
                hashes[os.path.relpath(path, PROJECT_ROOT)] = hashlib.sha256(f.read()).hexdigest()
    return hashes


@pytest.fixture(scope="session", autouse=True)
def project_folder_untouched():
    """Fails the run if any test created, changed or deleted a file in the
    project: tests that read or write files must do it under tmp_path."""
    before = project_files()
    yield
    after = project_files()
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    assert not changed, f"tests changed project files (use tmp_path instead): {changed}"


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
