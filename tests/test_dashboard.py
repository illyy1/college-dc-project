"""The live dashboard's logic, run without a window: Tk is replaced by a fake
that queues UI callbacks, time by a clock the tests move forward exactly,
the mic/camera by scripted signals, and GitHub by a stub. Covers the
monitoring loops, the timing rules at their exact boundaries, the
quiet-period watchdog, Pause/Resume, shutdown and adopting leftover calls."""

import json
import threading
from types import SimpleNamespace

import pytest

from hardware_monitor import dashboard
from hardware_monitor.config import (
    ALERT_COOLDOWN_SECONDS,
    ALERT_MIN_DISPLAY_SECONDS,
    COMPUTER_NAME,
    GITHUB_ISSUE_COOLDOWN_SECONDS,
    MAX_QUIET_SECONDS,
    SILENCE_DURATION,
    SILENCE_THRESHOLD,
)
from hardware_monitor.dashboard import HardwareMonitorApp
from hardware_monitor.github_client import IssueResult
from hardware_monitor.maintenance import MaintenanceDesk


# --- fakes ---

class FakeTclError(Exception):
    pass


class FakeWidget:
    def __init__(self, *args, **options):
        self.options = dict(options)

    def pack(self, **kwargs):
        pass

    def config(self, **options):
        self.options.update(options)


class FakeToplevel:
    created = []

    def __init__(self, root):
        self.exists, self.window_title = True, None
        FakeToplevel.created.append(self)

    def title(self, text):
        self.window_title = text

    def geometry(self, spec):
        self.spec = spec

    def attributes(self, *args):
        pass

    def configure(self, **kwargs):
        pass

    def winfo_screenwidth(self):
        return 1920

    def winfo_exists(self):
        return self.exists

    def destroy(self):
        self.exists = False


class FakeTk:
    """Queues after() callbacks the way Tk's main loop would run them."""

    def __init__(self):
        self.pending, self.destroyed = [], False

    def title(self, text):
        pass

    def geometry(self, spec):
        pass

    def protocol(self, name, handler):
        pass

    def after(self, delay, callback):
        if self.destroyed:
            raise FakeTclError("application has been destroyed")
        self.pending.append(callback)

    def flush(self):
        while self.pending:
            self.pending.pop(0)()

    def destroy(self):
        self.destroyed = True


FAKE_TK = SimpleNamespace(Tk=FakeTk, Label=FakeWidget, Button=FakeWidget, Toplevel=FakeToplevel, TclError=FakeTclError)


class FakeClock:
    """Stands in for the time module. sleep() moves time forward, lets the
    fake main loop run, and stops a monitoring loop after stop_after sleeps."""

    def __init__(self):
        self.now, self.app, self.sleeps, self.stop_after = 1000.0, None, 0, None

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.app.root.flush()
        self.sleeps += 1
        if self.stop_after is not None and self.sleeps >= self.stop_after:
            self.app.running = False

    def advance(self, seconds):
        self.now += seconds


class RunNow:
    """Stands in for threading.Thread: the GitHub post runs inline."""

    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


class ScriptedMic:
    """Returns the scripted values in order, then keeps repeating the last one."""

    def __init__(self):
        self.connected, self.volumes, self.calls, self.forced = [True], [0.2], 0, 0

    @staticmethod
    def _next(script):
        return script.pop(0) if len(script) > 1 else script[0]

    def is_connected(self):
        self.calls += 1
        return self._next(self.connected)

    def read_volume(self):
        return self._next(self.volumes)

    def force_fault(self):
        self.forced += 1


class ScriptedCamera(ScriptedMic):
    def __init__(self):
        super().__init__()
        self.frames_ok = [True]

    def read_frame_ok(self):
        return self._next(self.frames_ok)


@pytest.fixture
def env(monkeypatch, technicians, tmp_path):
    FakeToplevel.created = []
    clock = FakeClock()
    posted = []

    def fake_create_issue(token, repo, title, body, labels=()):
        posted.append(SimpleNamespace(key=labels[1], title=title, body=body))
        return IssueResult(True, issue_number=len(posted), url=f"https://example.invalid/{len(posted)}")

    monkeypatch.setattr(dashboard, "tk", FAKE_TK)
    monkeypatch.setattr(dashboard, "time", clock)
    monkeypatch.setattr(dashboard, "threading", SimpleNamespace(Lock=threading.Lock, Thread=RunNow))
    monkeypatch.setattr(dashboard, "create_issue", fake_create_issue)
    monkeypatch.setattr(dashboard, "get_github_token", lambda: "fake-token")

    def build(technicians=technicians, log_path=tmp_path / "maintenance.jsonl"):
        desk = MaintenanceDesk(technicians, log_path=str(log_path), report_csv_path=None)
        app = HardwareMonitorApp(maintenance_desk=desk)
        app.mic_source, app.camera_source = ScriptedMic(), ScriptedCamera()
        clock.app = app
        return app

    built = SimpleNamespace(clock=clock, posted=posted, build=build)
    built.app = build()
    return built


def run_loop(env, loop, iterations):
    """Runs a monitoring loop for exactly `iterations` passes."""
    env.app.running = True
    env.clock.sleeps, env.clock.stop_after = 0, iterations
    loop()
    env.clock.stop_after = None


def open_calls(app):
    return sorted((r.component_id, r.error_type) for r in app.maintenance_desk.open_requests())


def popup(app, key):
    win = app.active_alerts[key]
    return win if isinstance(win, FakeToplevel) and win.exists else None


# --- microphone loop ---

def test_mic_disconnect_opens_a_call_and_reconnect_resolves_it(env):
    app = env.app
    app.mic_source.connected = [False, True]
    run_loop(env, app.monitor_mic, 1)

    assert open_calls(app) == [("MIC-LOCAL", "MICROPHONE_DISCONNECTED")]
    assert popup(app, "mic_conn").window_title == "Microphone Disconnected"
    assert [p.key for p in env.posted] == ["mic_conn"]

    run_loop(env, app.monitor_mic, ALERT_MIN_DISPLAY_SECONDS)  # connected again, popup old enough
    assert open_calls(app) == [] and popup(app, "mic_conn") is None


def test_silence_alert_fires_only_after_more_than_the_silence_duration(env):
    app = env.app
    app.mic_source.volumes = [SILENCE_THRESHOLD / 2]
    # readings at 0, 1, ..., SILENCE_DURATION seconds: silent for exactly SILENCE_DURATION, not more
    run_loop(env, app.monitor_mic, SILENCE_DURATION + 1)
    assert open_calls(app) == []

    run_loop(env, app.monitor_mic, 1)  # one second longer
    assert open_calls(app) == [("MIC-LOCAL", "MICROPHONE_SILENCE")]
    assert popup(app, "mic_silence") is not None


def test_a_volume_exactly_at_the_threshold_is_not_silence(env):
    app = env.app
    app.mic_source.volumes = [SILENCE_THRESHOLD]
    run_loop(env, app.monitor_mic, SILENCE_DURATION + 5)
    assert open_calls(app) == [] and app.is_silent is False


def test_sound_returning_restarts_the_silence_timer(env):
    app = env.app
    quiet = SILENCE_THRESHOLD / 2
    app.mic_source.volumes = [quiet] * 5 + [0.2] + [quiet] * (SILENCE_DURATION + 1) + [quiet]
    run_loop(env, app.monitor_mic, 5 + 1 + SILENCE_DURATION + 1)
    assert open_calls(app) == []  # 14 s in total, but never more than 8 s in a row

    run_loop(env, app.monitor_mic, 1)
    assert open_calls(app) == [("MIC-LOCAL", "MICROPHONE_SILENCE")]


def test_a_disconnect_ends_a_silence_episode(env):
    app = env.app
    app.mic_source.volumes = [SILENCE_THRESHOLD / 2]
    app.mic_source.connected = [True] * (SILENCE_DURATION + 2) + [False]
    run_loop(env, app.monitor_mic, SILENCE_DURATION + 3)

    assert app.is_silent is False
    assert ("MIC-LOCAL", "MICROPHONE_DISCONNECTED") in open_calls(app)


# --- camera loop ---

@pytest.mark.parametrize("connected, frame_ok, title", [
    (False, True, "Camera Disconnected"),
    (True, False, "Camera Error"),
])
def test_camera_faults(env, connected, frame_ok, title):
    app = env.app
    app.camera_source.connected, app.camera_source.frames_ok = [connected], [frame_ok]
    run_loop(env, app.monitor_camera, 1)

    assert open_calls(app) == [("CAM-LOCAL", "CAMERA_FAILURE")]
    assert popup(app, "camera").window_title == title
    assert env.posted[0].title.endswith(title)


def test_camera_is_checked_every_three_seconds_and_recovers(env):
    app = env.app
    app.camera_source.connected = [False, True]
    run_loop(env, app.monitor_camera, 1)
    assert env.clock.now == 1003.0

    run_loop(env, app.monitor_camera, 2)  # 6 s later: healthy, popup old enough to close
    assert open_calls(app) == [] and popup(app, "camera") is None


@pytest.mark.parametrize("loop", ["monitor_mic", "monitor_camera"])
def test_paused_loops_do_not_check_the_hardware(env, loop):
    app = env.app
    app.is_paused = True
    run_loop(env, getattr(app, loop), 5)
    assert app.mic_source.calls == app.camera_source.calls == 0


# --- timing rules at their boundaries ---

def show_camera(app):
    app.show_alert("camera", "Camera Disconnected", "test")
    app.root.flush()


def test_a_popup_stays_up_for_the_minimum_display_time(env):
    app = env.app
    show_camera(app)

    env.clock.advance(ALERT_MIN_DISPLAY_SECONDS - 0.5)
    app.hide_alert("camera")
    app.root.flush()
    assert popup(app, "camera") is not None and open_calls(app) != []  # too soon: popup and call stay

    env.clock.advance(0.5)  # exactly the minimum
    app.hide_alert("camera")
    app.root.flush()
    assert popup(app, "camera") is None and open_calls(app) == []


def test_the_same_fault_cannot_pop_up_again_during_the_cooldown(env):
    app = env.app
    show_camera(app)
    env.clock.advance(ALERT_MIN_DISPLAY_SECONDS)
    app.hide_alert("camera")
    app.root.flush()

    env.clock.advance(ALERT_COOLDOWN_SECONDS - 0.5)
    show_camera(app)
    assert popup(app, "camera") is None
    assert open_calls(app) == [("CAM-LOCAL", "CAMERA_FAILURE")]  # the call is still opened, no fault goes unrecorded

    env.clock.advance(0.5)  # cooldown over
    show_camera(app)
    assert popup(app, "camera") is not None


def test_at_most_one_github_issue_per_fault_within_the_cooldown(env, capsys):
    app = env.app

    def new_camera_fault():
        app.show_alert("camera", "Camera Disconnected", "test")
        app.resolve_maintenance_call("camera")  # cleared, so the next fault is a new call

    new_camera_fault()
    env.clock.advance(GITHUB_ISSUE_COOLDOWN_SECONDS - 1)
    new_camera_fault()
    assert len(env.posted) == 1
    assert "GitHub issue for camera skipped" in capsys.readouterr().out

    env.clock.advance(1)  # exactly the cooldown
    new_camera_fault()
    assert len(env.posted) == 2


def test_an_ongoing_fault_reuses_its_call_and_issue(env):
    app = env.app
    for _ in range(3):
        show_camera(app)
    assert len(app.maintenance_desk.requests) == 1 and len(env.posted) == 1


def test_github_issue_names_the_call_and_technician(env):
    app = env.app
    show_camera(app)
    request = app.open_requests["camera"]
    technician = app.maintenance_desk.technician_for(request)
    assert f"`{request.request_id}`" in env.posted[0].body and technician.name in env.posted[0].body


def test_alerts_still_show_when_no_technician_can_take_the_call(env, capsys):
    app = env.build(technicians={})
    app.show_alert("camera", "Camera Disconnected", "test")
    app.root.flush()

    assert "Could not open a maintenance call for camera" in capsys.readouterr().out
    assert popup(app, "camera") is not None and len(env.posted) == 1


# --- quiet-period watchdog ---

def test_watchdog_forces_a_fault_after_the_quiet_period(env):
    app = env.app
    run_loop(env, app.keep_room_busy, MAX_QUIET_SECONDS - 1)
    assert app.mic_source.forced + app.camera_source.forced == 0

    run_loop(env, app.keep_room_busy, 1)  # exactly MAX_QUIET_SECONDS without a popup
    assert app.mic_source.forced + app.camera_source.forced == 1


def test_watchdog_only_forces_a_device_free_to_show_a_popup(env):
    app = env.app
    app.active_alerts["mic_conn"] = "pending"  # the mic already has a popup
    run_loop(env, app.keep_room_busy, MAX_QUIET_SECONDS)
    assert (app.mic_source.forced, app.camera_source.forced) == (0, 1)


@pytest.mark.parametrize("blocker", ["slots_full", "cooling_down"])
def test_watchdog_waits_when_no_popup_could_appear(env, blocker):
    app = env.app
    if blocker == "slots_full":
        app._popup_slots = {"other_a": 0, "other_b": 1}
    else:
        app._closed_at = {"mic_conn": env.clock.now + MAX_QUIET_SECONDS, "camera": env.clock.now + MAX_QUIET_SECONDS}
    run_loop(env, app.keep_room_busy, MAX_QUIET_SECONDS + 5)
    assert app.mic_source.forced + app.camera_source.forced == 0


def test_a_break_does_not_count_as_quiet_time(env):
    app = env.app
    app.is_paused = True
    run_loop(env, app.keep_room_busy, MAX_QUIET_SECONDS * 2)
    app.is_paused = False
    run_loop(env, app.keep_room_busy, MAX_QUIET_SECONDS - 1)
    assert app.mic_source.forced + app.camera_source.forced == 0


# --- pause / resume ---

def test_pause_hides_popups_but_keeps_calls_open(env):
    app = env.app
    show_camera(app)
    app.toggle_pause()
    app.root.flush()

    assert popup(app, "camera") is None
    assert open_calls(app) == [("CAM-LOCAL", "CAMERA_FAILURE")]
    assert app.status_lbl.options["text"] == "Monitoring Paused"
    assert app.pause_btn.options["text"] == "Resume Monitoring"


def test_no_new_alerts_while_paused(env):
    app = env.app
    app.toggle_pause()
    show_camera(app)
    assert open_calls(app) == [] and env.posted == []


def test_pause_before_a_popup_is_drawn_cancels_it(env):
    app = env.app
    app.show_alert("camera", "Camera Disconnected", "test")  # popup reserved, not yet drawn
    app.toggle_pause()
    app.root.flush()

    assert FakeToplevel.created == []
    assert app.active_alerts["camera"] is None and app._popup_slots == {}


def test_resume_restarts_the_silence_timer(env):
    app = env.app
    app.toggle_pause()
    env.clock.advance(100)
    app.toggle_pause()

    assert app.silence_start == env.clock.now
    assert app.status_lbl.options["text"] == "Monitoring Active"


# --- shutdown ---

def test_after_closing_alerts_are_ignored(env):
    app = env.app
    app.stop()
    assert app.running is False and app.root.destroyed

    app.show_alert("camera", "Camera Disconnected", "test")
    app.hide_alert("camera")
    assert open_calls(app) == [] and env.posted == []


def test_a_ui_error_while_running_is_not_hidden(env):
    app = env.app
    app.root.destroyed = True  # the window broke while monitoring is still on
    with pytest.raises(FakeTclError):
        app._run_on_ui_thread(lambda: None)


def test_a_ui_error_during_shutdown_is_ignored(env):
    app = env.app
    app.root.destroyed = True
    app.running = True

    def closing_after_check():
        app.running = False  # stop() runs between the running check and after()
        raise FakeTclError("application has been destroyed")

    app.root.after = lambda delay, callback: closing_after_check()
    app._run_on_ui_thread(lambda: None)  # must not raise


# --- calls left open by a previous run ---

def test_leftover_calls_are_adopted_and_duplicates_closed(env, tmp_path, capsys):
    log_path = tmp_path / "leftover.jsonl"
    record = {"status": "IN_PROGRESS", "workstation_id": COMPUTER_NAME, "component_id": "CAM-LOCAL",
              "error_type": "CAMERA_FAILURE", "assigned_tech_id": "T1", "priority": 1}
    log_path.write_text("\n".join(json.dumps(r) for r in [
        {**record, "request_id": "MR-0001", "opened_at": "2026-10-01T09:00:00"},
        {**record, "request_id": "MR-0002", "opened_at": "2026-10-01T10:00:00"},
    ]) + "\n", encoding="utf-8")

    app = env.build(log_path=log_path)
    assert app.open_requests["camera"].request_id == "MR-0001"  # the oldest is kept
    assert "Closed duplicate maintenance call MR-0002" in capsys.readouterr().out
    assert [r.request_id for r in app.maintenance_desk.open_requests()] == ["MR-0001"]

    run_loop(env, app.monitor_camera, 1)  # camera healthy: the adopted call is resolved
    assert app.maintenance_desk.open_requests() == []
