"""TDD specification for dependency injection: the components get their
dependencies from outside, through their constructor or parameters, instead
of creating fixed implementations themselves. Each keeps the real one as its
default, so main.py still calls HardwareMonitorApp() unchanged.

What hardware_monitor/dashboard.py must provide:
- HardwareMonitorApp(maintenance_desk=None, *, alert_manager=None,
      mic_source=None, camera_source=None, github_token=READ_FROM_ENV,
      gemini_api_key=READ_FROM_ENV, report_fault=None, clock=None,
      start_thread=None)
    Each is kept as the attribute of the same name and is the one used:
    alerts are logged through alert_manager, the monitors read mic_source
    and camera_source, faults are reported through report_fault (with
    github_token and gemini_api_key), every reading of the time and every
    wait goes through clock (time() and sleep()), and every background task
    is started with start_thread(target).
    The defaults: AlertManager(), SyntheticMicrophoneSource(),
    SyntheticCameraSource(), the keys read from .env (get_github_token(),
    get_gemini_api_key()) only when not passed (None means "no key"),
    services.report_fault, the time module, and start_daemon_thread.
- start_daemon_thread(target): runs target in a new daemon thread.

And services.report_fault(..., *, create_issue=api_client.create_issue,
find_open_issues=api_client.find_open_issues,
triage_fault=api_client.triage_fault): the API calls it makes go through
these, so another client can be passed in.

Only Tk is still replaced at module level (fake_tk): the window toolkit is
the edge of the program. Everything else is passed in."""

import inspect
import threading
import time

import pytest

from conftest import encrypt_token, make_triage
from fake_dashboard import FakeClock, ScriptedCamera, ScriptedMic
from fake_tk import FAKE_TK, FakeToplevel
from hardware_monitor import api_client, dashboard, services
from hardware_monitor.api_client import IssueResult, IssueSearchResult, OpenIssue, TriageResult
from hardware_monitor.maintenance import MaintenanceDesk
from hardware_monitor.processing import AlertManager
from hardware_monitor.synthetic_sources import SyntheticCameraSource, SyntheticMicrophoneSource


def run_now(target):
    target()


@pytest.fixture
def parts(monkeypatch, technicians, tmp_path, github):
    """What a dashboard is built from: a desk on tmp_path, a fake clock, and
    a builder that passes everything else in. Only Tk is patched."""
    monkeypatch.setattr("hardware_monitor.dashboard.tk", FAKE_TK)
    FakeToplevel.created = []
    clock = FakeClock()
    desk = MaintenanceDesk(technicians, log_path=str(tmp_path / "maintenance.jsonl"), report_csv_path=None)

    def build(**deps):
        injected = dict(clock=clock, start_thread=run_now, github_token="test-token", gemini_api_key=None,
                        mic_source=ScriptedMic(), camera_source=ScriptedCamera(),
                        alert_manager=AlertManager(log_path=str(tmp_path / "alerts.jsonl")))
        app = dashboard.HardwareMonitorApp(maintenance_desk=desk, **{**injected, **deps})
        clock.app = app
        return app

    class Parts:
        pass

    built = Parts()
    built.clock, built.desk, built.github, built.build = clock, desk, github, build
    return built


def run_loop(app, clock, loop, iterations):
    app.running = True
    clock.sleeps, clock.stop_after = 0, iterations
    loop()
    clock.stop_after = None


class RecordingReporter:
    """Stands in for services.report_fault: records each fault, posts nothing."""

    def __init__(self):
        self.faults = []

    def __call__(self, desk, github_token, gemini_api_key, alert_key, title, message, request=None):
        self.faults.append((desk, github_token, gemini_api_key, alert_key, title, message, request))
        return services.FaultReport(IssueResult(True, issue_number=7, url="https://github.com/o/r/issues/7"))


# --- the dashboard: every dependency passed in is the one used ---

def test_each_dependency_is_kept_as_given(parts):
    mic, camera, manager, reporter = ScriptedMic(), ScriptedCamera(), AlertManager(), RecordingReporter()
    app = parts.build(mic_source=mic, camera_source=camera, alert_manager=manager, report_fault=reporter)

    assert app.mic_source is mic and app.camera_source is camera
    assert app.alert_manager is manager and app.report_fault is reporter
    assert app.clock is parts.clock and app.start_thread is run_now


def test_the_monitors_read_the_injected_sources(parts):
    app = parts.build()
    run_loop(app, parts.clock, app.monitor_mic, 1)
    run_loop(app, parts.clock, app.monitor_camera, 1)
    assert app.mic_source.calls > 0 and app.camera_source.calls > 0


def test_alerts_are_logged_through_the_injected_alert_manager(parts):
    app = parts.build()
    app.open_maintenance_call("camera", "no frame")
    assert [a.component_id for a in app.alert_manager.alert_history] == ["CAM-LOCAL"]


def test_time_comes_from_the_injected_clock(parts):
    app = parts.build()
    assert app.silence_start == parts.clock.now  # 1000.0, not the real time
    run_loop(app, parts.clock, app.monitor_mic, 2)
    assert parts.clock.sleeps == 2  # the waits went to the clock, not time.sleep


def test_faults_are_reported_through_the_injected_reporter(parts):
    reporter = RecordingReporter()
    app = parts.build(report_fault=reporter, github_token="injected-token", gemini_api_key="injected-key")
    app.send_github_alert("camera", "Camera Disconnected", "no frame")

    assert reporter.faults == [(parts.desk, "injected-token", "injected-key", "camera", "Camera Disconnected",
                                "no frame", None)]
    assert parts.github.calls == []  # the real reporter was not used


def test_background_work_is_started_through_start_thread(parts):
    started, reporter = [], RecordingReporter()
    app = parts.build(report_fault=reporter, start_thread=started.append)
    app.send_github_alert("camera", "Camera Disconnected", "no frame")

    assert len(started) == 1 and reporter.faults == []  # handed over, not run yet
    started[0]()
    assert len(reporter.faults) == 1


@pytest.mark.parametrize("token, key", [("injected-token", "injected-key"), (None, None)],
                         ids=["keys-given", "no-keys"])
def test_passed_keys_are_used_and_env_is_not_read(parts, monkeypatch, token, key):
    def must_not_read():
        raise AssertionError(".env was read although the keys were passed in")

    monkeypatch.setattr("hardware_monitor.dashboard.get_github_token", must_not_read)
    monkeypatch.setattr("hardware_monitor.dashboard.get_gemini_api_key", must_not_read)
    app = parts.build(github_token=token, gemini_api_key=key)
    assert (app.github_token, app.gemini_api_key) == (token, key)


def test_a_new_kind_of_source_works_without_changing_the_dashboard(parts):
    class UnpluggedUsbCamera:
        """Not one of the project's sources: anything with these two methods will do."""

        def is_connected(self):
            return False

        def read_frame_ok(self):
            return False

    app = parts.build(camera_source=UnpluggedUsbCamera())
    run_loop(app, parts.clock, app.monitor_camera, 1)

    assert [(r.component_id, r.error_type) for r in app.maintenance_desk.open_requests()] == \
        [("CAM-LOCAL", "CAMERA_FAILURE")]


# --- the dashboard's defaults: the real implementations ---

def test_without_arguments_the_real_implementations_are_used(parts, monkeypatch):
    token, key = encrypt_token("token-from-env")
    monkeypatch.setenv("GITHUB_TOKEN", token)
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key-from-env")
    app = dashboard.HardwareMonitorApp(maintenance_desk=parts.desk)

    assert isinstance(app.mic_source, SyntheticMicrophoneSource)
    assert isinstance(app.camera_source, SyntheticCameraSource)
    assert isinstance(app.alert_manager, AlertManager)
    assert app.report_fault is services.report_fault
    assert app.clock is time and app.start_thread is dashboard.start_daemon_thread
    assert (app.github_token, app.gemini_api_key) == ("token-from-env", "gemini-key-from-env")


def test_start_daemon_thread_runs_the_target_in_a_daemon_thread():
    ran, done = [], threading.Event()

    def target():
        ran.append((threading.current_thread() is not threading.main_thread(), threading.current_thread().daemon))
        done.set()

    dashboard.start_daemon_thread(target)
    assert done.wait(5) and ran == [(True, True)]


# --- services.report_fault: the API clients are passed in ---

class FakeApis:
    """Stand-ins for api_client's three calls, recording what they were asked."""

    def __init__(self, open_issues=()):
        self.open_issues, self.created, self.triaged, self.searched = open_issues, [], [], []

    def find_open_issues(self, token, repo, labels):
        self.searched.append(tuple(labels))
        return IssueSearchResult(True, issues=tuple(self.open_issues))

    def triage_fault(self, api_key, title, details, component):
        self.triaged.append(title)
        return TriageResult(True, triage=make_triage(priority=2))

    def create_issue(self, token, repo, title, body, labels=()):
        self.created.append(list(labels))
        return IssueResult(True, issue_number=5, url="https://github.com/o/r/issues/5")

    def clients(self):
        return dict(create_issue=self.create_issue, find_open_issues=self.find_open_issues,
                    triage_fault=self.triage_fault)


def report(desk, apis, gemini_api_key="gemini-key"):
    return services.report_fault(desk, "token", gemini_api_key, "camera", "Camera Disconnected", "no frame",
                                 **apis.clients())


def test_report_fault_uses_the_clients_passed_in(desk, github, gemini):
    apis = FakeApis()
    outcome = report(desk, apis)

    assert outcome.issue.issue_number == 5 and outcome.priority == 2
    assert apis.searched == [("hardware-alert", "camera")]
    assert apis.triaged == ["Camera Disconnected"]
    assert apis.created == [["hardware-alert", "camera", "priority-2"]]
    assert github.calls == [] and github.search.calls == [] and gemini.calls == []  # no real client used


def test_report_fault_decides_the_same_way_with_other_clients(desk):
    title = services.issue_title("Camera Disconnected")
    apis = FakeApis(open_issues=[OpenIssue(9, "https://github.com/o/r/issues/9", title)])
    outcome = report(desk, apis)

    assert outcome.already_open and outcome.issue.issue_number == 9
    assert apis.created == [] and apis.triaged == []


@pytest.mark.parametrize("name", ["create_issue", "find_open_issues", "triage_fault"])
def test_report_fault_defaults_to_the_real_api_client(name):
    parameter = inspect.signature(services.report_fault).parameters[name]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is getattr(api_client, name)
