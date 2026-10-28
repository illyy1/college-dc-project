import hashlib
import json
import logging
import os
import socket
import sys
import types
from unittest import mock

import pytest

PROJECT_ROOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# The package __init__ imports the Tkinter dashboard. Some environments (CI
# images, containers) ship Python without Tk, so stub it; the tests never
# open a window.
try:
    import tkinter  # noqa: F401
except ImportError:
    sys.modules["tkinter"] = types.ModuleType("tkinter")

from hardware_monitor.maintenance import MaintenanceDesk  # noqa: E402
from hardware_monitor.models import Camera, MaintenanceRequest, Microphone, Technician, Workstation  # noqa: E402
from hardware_monitor.processing import Alert, AlertManager  # noqa: E402
from hardware_monitor.repository import (  # noqa: E402
    load_workstations_from_jsonl,
    load_technicians_from_jsonl,
)

# the real data files: their contents change as the project evolves
SAMPLE_DATA_PATH = os.path.join(PROJECT_ROOT, "data", "sample_data.jsonl")
TECHNICIANS_PATH = os.path.join(PROJECT_ROOT, "data", "technicians.jsonl")
# a frozen copy of the fleet the tests own, for tests that need exact faults
TEST_FLEET_PATH = os.path.join(PROJECT_ROOT, "tests", "data", "sample_fleet.jsonl")


# --- reusable data: the synthetic fleet and technicians from data/ ---

@pytest.fixture
def workstations():
    """The test fleet (tests/data/sample_fleet.jsonl): 8 workstations, 16
    components, with known faults. A frozen copy, so editing the real
    data/sample_data.jsonl can't break tests that need exact numbers."""
    return load_workstations_from_jsonl(TEST_FLEET_PATH)


@pytest.fixture
def real_workstations():
    """The fleet in data/sample_data.jsonl, whatever it holds right now."""
    return load_workstations_from_jsonl(SAMPLE_DATA_PATH)


@pytest.fixture
def real_technicians():
    """The technicians in data/technicians.jsonl, however many there are:
    staff get hired and leave, so tests using this assume nothing about who
    or how many."""
    return load_technicians_from_jsonl(TECHNICIANS_PATH)


TEAM_NAMES = ("Dana Levi", "Yossi Cohen", "Noa Mizrahi", "Avi Peretz")


@pytest.fixture
def make_team():
    """make_team(n): a test team of n technicians, T1..Tn, independent of
    data/technicians.jsonl. The first four are named as in TEAM_NAMES."""
    def build(size):
        return {f"T{n}": Technician(f"T{n}", TEAM_NAMES[n - 1] if n <= len(TEAM_NAMES) else f"Technician {n}")
                for n in range(1, size + 1)}
    return build


@pytest.fixture
def technicians(make_team):
    """A fixed four-person test team (T1 Dana Levi, T2 Yossi Cohen, T3 Noa
    Mizrahi, T4 Avi Peretz) for tests that need exact numbers. It is not
    read from data/technicians.jsonl, so hiring or firing staff can't break
    those tests."""
    return make_team(4)


@pytest.fixture
def technicians_file(technicians, write_jsonl):
    """The `technicians` test team written to a temporary technicians.jsonl."""
    return write_jsonl("technicians.jsonl", *({"tech_id": t.tech_id, "name": t.name} for t in technicians.values()))


# --- reusable mock objects ---

class AlertFactory:
    """Builds fresh Alert objects. Each call is a new alert (new alert_id);
    any field can be overridden."""

    def make(self, component_id="CAM-104", workstation_id="WS-104", error_type="CAMERA_FAILURE",
             message="test fault", priority=1, **fields):
        return Alert(component_id, workstation_id, error_type, message, priority=priority, **fields)

    def camera(self, **fields):
        """A lost camera on WS-104."""
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


# --- reusable maintenance-log records (the statistics tests) ---

@pytest.fixture
def make_call():
    """Builds one maintenance-log record, shaped like the ones MaintenanceDesk writes:
    make_call(number, error_type, tech_id, status, opened_at="09:00", workstation_id="WS-104",
    **other_fields)."""
    names = {"T1": "Dana Levi", "T2": "Yossi Cohen", "T3": "Noa Mizrahi", "T4": "Avi Peretz"}

    def build(number, error_type, tech_id, status, opened_at="09:00", workstation_id="WS-104", **fields):
        return {**{
            "request_id": f"MR-{number:04d}", "alert_id": number, "priority": 1,
            "error_type": error_type, "workstation_id": workstation_id, "component_id": "CAM-104",
            "status": status, "assigned_tech_id": tech_id, "assigned_tech_name": names.get(tech_id),
            "opened_at": f"2026-10-20T{opened_at}:00",
            "resolved_at": f"2026-10-20T{opened_at}:30" if status == "RESOLVED" else None,
        }, **fields}

    return build


# one field of the wrong type, as a hand-edited or damaged log could hold it
# (the app itself never writes these); make_bad_call puts one in a saved call
OPEN_TIME_A_NUMBER = {"opened_at": 5}
BAD_FIELDS = [
    pytest.param(OPEN_TIME_A_NUMBER, id="open-time-a-number"),
    pytest.param({"opened_at": "yesterday"}, id="open-time-not-a-date"),
    pytest.param({"assigned_tech_id": 7}, id="technician-id-a-number"),
    pytest.param({"assigned_tech_id": ["T1"]}, id="technician-id-a-list"),
    pytest.param({"error_type": 3}, id="error-type-a-number"),
    pytest.param({"error_type": ["CAMERA_FAILURE"]}, id="error-type-a-list"),
    pytest.param({"request_id": ["MR-0008"]}, id="request-id-a-list"),
]


@pytest.fixture
def make_bad_call(make_call):
    """make_bad_call(number, tech_id, bad_field=OPEN_TIME_A_NUMBER): an open
    camera call, built by make_call, with that wrongly typed field."""
    def build(number, tech_id, bad_field=OPEN_TIME_A_NUMBER):
        return {**make_call(number, "CAMERA_FAILURE", tech_id, "IN_PROGRESS"), **bad_field}
    return build


@pytest.fixture
def reports_log(make_call, write_jsonl):
    """A saved maintenance log for the report tests: Dana's call MR-0001, opened
    then resolved, and MR-0002, still open, by T42, who is no longer on the team."""
    return write_jsonl(
        "reports/maintenance_requests.jsonl",
        make_call(1, "CAMERA_FAILURE", "T1", "IN_PROGRESS", "09:00"),
        make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED", "09:00"),
        make_call(2, "MICROPHONE_FAILURE", "T42", "IN_PROGRESS", "10:00", workstation_id="WS-107",
                  component_id="MIC-107"),
    )


def as_requests(records):
    """What load_maintenance_history gives for these log records (raw dicts,
    e.g. from make_call): one MaintenanceRequest each, built by from_dict."""
    return [MaintenanceRequest.from_dict(record) for record in records]


@pytest.fixture
def history(make_call):
    """Seven raw log records (as_requests turns them into calls): 4 camera,
    2 microphone, 1 other; 3 still open.
    Dana 3 calls (1 open), Yossi 2 (1 open), Noa 1 (open), Avi 1 (resolved)."""
    return [
        make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED", "09:00"),
        make_call(2, "CAMERA_FAILURE", "T3", "IN_PROGRESS", "09:10", workstation_id="WS-107"),
        make_call(3, "MICROPHONE_DISCONNECTED", "T2", "RESOLVED", "09:20", workstation_id="WS-105"),
        make_call(4, "MICROPHONE_SILENCE", "T2", "IN_PROGRESS", "09:30", workstation_id="WS-105"),
        make_call(5, "CAMERA_FAILURE", "T1", "RESOLVED", "09:40"),
        make_call(6, "MONITOR_OFF", "T4", "RESOLVED", "09:50", workstation_id="WS-101"),
        make_call(7, "CAMERA_FAILURE", "T1", "IN_PROGRESS", "10:00"),
    ]


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

    # socket.socket.connect is defined in C (_socket), so PyCharm's stubs can't see it; it exists
    # noinspection PyUnresolvedReferences
    with mock.patch.object(socket, "getaddrinfo", getaddrinfo), \
            mock.patch.object(socket.socket, "connect", connect):
        yield


@pytest.fixture(autouse=True)
def no_real_secrets(monkeypatch):
    """No test can read the real .env or a token from the environment;
    tests that need a token set their own (encrypted with a throwaway key)."""
    try:
        monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    except ImportError:  # the demo-only install has no python-dotenv
        pass
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def no_real_monitor_log(monkeypatch, tmp_path):
    """configure_logging() (main.main() calls it) writes under tmp_path, never
    logs/monitor.log, and the handlers it adds are removed after the test."""
    monkeypatch.setattr("hardware_monitor.safe_logging.MONITOR_LOG_PATH", str(tmp_path / "monitor.log"))
    logger = logging.getLogger("hardware_monitor")
    handlers, level = list(logger.handlers), logger.level
    yield
    for handler in logger.handlers:
        if handler not in handlers:
            handler.close()
    logger.handlers[:] = handlers
    logger.setLevel(level)


@pytest.fixture
def logged(caplog):
    """What the package logged (INFO and up) since the last call, one message
    per line: the logging counterpart of capsys.readouterr().out."""
    caplog.set_level(logging.INFO, logger="hardware_monitor")

    def read():
        text = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("hardware_monitor"))
        caplog.clear()
        return text

    return read


@pytest.fixture(autouse=True)
def retry_waits():
    """Replaces the sleep between retries (GitHub, log and report writes) with
    a mock, so retries cost no time. Yields the list of waits that would have
    happened."""
    waits = []
    with mock.patch("hardware_monitor.retry.sleep", side_effect=waits.append):
        yield waits


# --- shared stand-ins: one object per purpose ---

@pytest.fixture
def write_jsonl(tmp_path):
    """write_jsonl(name, *lines, fragment="", append=False) writes a JSONL
    file under tmp_path and returns its path. Lines are dicts (written as
    JSON) or strings (written as they are, e.g. a broken line); `fragment`
    is a last line cut off mid-write, with no newline."""
    def write(name, *lines, fragment="", append=False):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines) + fragment
        with open(path, "a" if append else "w", encoding="utf-8") as f:
            f.write(text)
        return str(path)
    return write


def encrypt_token(secret):
    """(encrypted token, key): a GitHub token encrypted the way .env holds
    it, with a throwaway key."""
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    return Fernet(key).encrypt(secret.encode()).decode(), key.decode()


def run_together(worker, count=8):
    """Starts `count` threads running worker() at the same moment, waits for
    all of them, and fails if any raised or got stuck."""
    import threading
    barrier = threading.Barrier(count)
    errors = []

    def start():
        barrier.wait()
        try:
            worker()
        except Exception as e:  # surfaced by the assertion below
            errors.append(e)

    threads = [threading.Thread(target=start) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "threads did not finish (deadlock?)"
    assert errors == []


@pytest.fixture
def slow_requests(monkeypatch):
    """slow_requests(seconds): every new MaintenanceRequest first pauses, which
    widens the window between reading and advancing the call number, so an
    unlocked race would show up every time. The real request is still built."""
    import time
    from hardware_monitor import maintenance

    def slow_down(seconds):
        real_request = maintenance.MaintenanceRequest

        class SlowRequest(real_request):
            def __init__(self, *args, **kwargs):
                time.sleep(seconds)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr("hardware_monitor.maintenance.MaintenanceRequest", SlowRequest)

    return slow_down


@pytest.fixture
def github():
    """requests.post, mocked: see fake_github.MockedGitHub. Its .search is
    requests.get, mocked too (fake_github.MockedGitHubSearch): by default no
    issue is open."""
    from fake_github import MockedGitHub, MockedGitHubSearch
    with mock.patch("hardware_monitor.api_client.requests.post", autospec=True) as post, \
            mock.patch("hardware_monitor.api_client.requests.get", autospec=True) as get:
        github = MockedGitHub(post)
        github.search = MockedGitHubSearch(get)
        yield github


GEMINI_KEY = "secret-gemini-key"


def make_triage(priority=2, cause="cable", minutes=15):
    """An api_client.Triage, as triage_fault returns it, for the code that
    acts on one (services.apply_triage)."""
    from hardware_monitor.api_client import Triage
    return Triage(priority, cause, minutes, ("Reseat the USB cable.",))


@pytest.fixture
def ai_dash(dash, gemini):
    """The shared `dash`, with the Gemini key GEMINI_KEY; .gemini is the
    mocked Gemini."""
    dash.app.gemini_api_key = GEMINI_KEY
    dash.gemini = gemini
    return dash


@pytest.fixture
def gemini():
    """Gemini's post, mocked: see fake_gemini.MockedGemini."""
    from fake_gemini import MockedGemini
    with mock.patch("hardware_monitor.api_client.post", autospec=True) as post:
        yield MockedGemini(post)


@pytest.fixture
def dash(monkeypatch, technicians, tmp_path, github):
    """A real dashboard, built by its own constructor, with only its external
    dependencies replaced: Tk (fake_tk, patched: the window toolkit is the
    program's edge), and passed in through the constructor the clock, a
    thread starter that runs each task inline and the mic/camera
    (fake_dashboard); GitHub is `github`, and the token is encrypted with a
    throwaway key for the real get_github_token.

    .app is the dashboard, .clock its clock, .github the mocked GitHub
    (.github.posted: the issues sent); .build(technicians=..., log_path=...,
    **dependencies) builds another one."""
    from types import SimpleNamespace
    from fake_dashboard import FakeClock, ScriptedCamera, ScriptedMic, run_now
    from fake_tk import FAKE_TK, FakeToplevel
    from hardware_monitor.dashboard import HardwareMonitorApp

    FakeToplevel.created = []
    clock = FakeClock()
    token, key = encrypt_token("test-token")
    monkeypatch.setenv("GITHUB_TOKEN", token)
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    monkeypatch.setattr("hardware_monitor.dashboard.tk", FAKE_TK)

    def build(technicians=technicians, log_path=tmp_path / "maintenance.jsonl", **dependencies):
        """Another dashboard; any constructor argument given replaces the fixture's."""
        desk = MaintenanceDesk(technicians, log_path=str(log_path), report_csv_path=None)
        injected = dict(clock=clock, start_thread=run_now, mic_source=ScriptedMic(), camera_source=ScriptedCamera())
        app = HardwareMonitorApp(maintenance_desk=desk, **{**injected, **dependencies})
        clock.app = app
        return app

    built = SimpleNamespace(clock=clock, github=github, build=build)
    built.app = build()
    return built
