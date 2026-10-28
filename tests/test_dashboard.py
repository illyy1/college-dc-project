"""The live dashboard's logic, run without a window. Only external
dependencies are replaced: Tk by a fake that queues UI callbacks, time by a
clock the tests move forward exactly, the mic/camera by scripted signals,
and requests.post by a mock. Everything in the project, including
create_issue and get_github_token, runs for real. Covers the monitoring
loops, the timing rules at their exact boundaries, the quiet-period
watchdog, Pause/Resume, shutdown and adopting leftover calls."""


import pytest

from hardware_monitor.config import (
    MAX_VISIBLE_ALERTS,
    ALERT_COOLDOWN_SECONDS,
    ALERT_MIN_DISPLAY_SECONDS,
    COMPUTER_NAME,
    GITHUB_ISSUE_COOLDOWN_SECONDS,
    MAX_QUIET_SECONDS,
    SILENCE_DURATION,
    SILENCE_THRESHOLD,
)

from fake_tk import FakeTclError, FakeToplevel


# The dashboard under test is the shared `dash` fixture (conftest.py).


def run_loop(dash, loop, iterations):
    """Runs a monitoring loop for exactly `iterations` passes."""
    dash.app.running = True
    dash.clock.sleeps, dash.clock.stop_after = 0, iterations
    loop()
    dash.clock.stop_after = None


def open_calls(app):
    return sorted((r.component_id, r.error_type) for r in app.maintenance_desk.open_requests())


def popup(app, key):
    win = app.active_alerts[key]
    return win if isinstance(win, FakeToplevel) and win.exists else None


def popup_title(app, key):
    """The title of the popup showing for `key`, or None if there is none."""
    win = popup(app, key)
    return win.window_title if win is not None else None


def test_the_dashboard_defaults():
    # the one place the defaults are pinned; every other test reads them from config
    assert (SILENCE_DURATION, ALERT_MIN_DISPLAY_SECONDS, ALERT_COOLDOWN_SECONDS, GITHUB_ISSUE_COOLDOWN_SECONDS,
            MAX_QUIET_SECONDS, MAX_VISIBLE_ALERTS) == (8, 5, 10, 600, 45, 2)


# --- microphone loop ---

def test_mic_disconnect_opens_a_call_and_reconnect_resolves_it(dash):
    app = dash.app
    app.mic_source.connected = [False, True]
    run_loop(dash, app.monitor_mic, 1)

    assert open_calls(app) == [("MIC-LOCAL", "MICROPHONE_DISCONNECTED")]
    assert popup_title(app, "mic_conn") == "Microphone Disconnected"
    assert [p.key for p in dash.github.posted] == ["mic_conn"]

    run_loop(dash, app.monitor_mic, ALERT_MIN_DISPLAY_SECONDS)  # connected again, popup old enough
    assert open_calls(app) == [] and popup(app, "mic_conn") is None


def test_silence_alert_fires_only_after_more_than_the_silence_duration(dash):
    app = dash.app
    app.mic_source.volumes = [SILENCE_THRESHOLD / 2]
    # readings at 0, 1, ..., SILENCE_DURATION seconds: silent for exactly SILENCE_DURATION, not more
    run_loop(dash, app.monitor_mic, SILENCE_DURATION + 1)
    assert open_calls(app) == []

    run_loop(dash, app.monitor_mic, 1)  # one second longer
    assert open_calls(app) == [("MIC-LOCAL", "MICROPHONE_SILENCE")]
    assert popup(app, "mic_silence") is not None


def test_a_volume_exactly_at_the_threshold_is_not_silence(dash):
    app = dash.app
    app.mic_source.volumes = [SILENCE_THRESHOLD]
    run_loop(dash, app.monitor_mic, SILENCE_DURATION + 5)
    assert open_calls(app) == [] and app.is_silent is False


def test_sound_returning_restarts_the_silence_timer(dash):
    app = dash.app
    quiet = SILENCE_THRESHOLD / 2
    app.mic_source.volumes = [quiet] * 5 + [0.2] + [quiet] * (SILENCE_DURATION + 1) + [quiet]
    run_loop(dash, app.monitor_mic, 5 + 1 + SILENCE_DURATION + 1)
    assert open_calls(app) == []  # 14 s in total, but never more than 8 s in a row

    run_loop(dash, app.monitor_mic, 1)
    assert open_calls(app) == [("MIC-LOCAL", "MICROPHONE_SILENCE")]


def test_a_disconnect_ends_a_silence_episode(dash):
    app = dash.app
    app.mic_source.volumes = [SILENCE_THRESHOLD / 2]
    app.mic_source.connected = [True] * (SILENCE_DURATION + 2) + [False]
    run_loop(dash, app.monitor_mic, SILENCE_DURATION + 3)

    assert app.is_silent is False
    assert ("MIC-LOCAL", "MICROPHONE_DISCONNECTED") in open_calls(app)


# --- camera loop ---

@pytest.mark.parametrize("connected, frame_ok, title", [
    (False, True, "Camera Disconnected"),
    (True, False, "Camera Error"),
])
def test_camera_faults(dash, connected, frame_ok, title):
    app = dash.app
    app.camera_source.connected, app.camera_source.frames_ok = [connected], [frame_ok]
    run_loop(dash, app.monitor_camera, 1)

    assert open_calls(app) == [("CAM-LOCAL", "CAMERA_FAILURE")]
    assert popup_title(app, "camera") == title
    assert dash.github.posted[0].title.endswith(title)


def test_camera_is_checked_every_three_seconds_and_recovers(dash):
    app = dash.app
    app.camera_source.connected = [False, True]
    run_loop(dash, app.monitor_camera, 1)
    assert dash.clock.now == 1003.0

    run_loop(dash, app.monitor_camera, 2)  # 6 s later: healthy, popup old enough to close
    assert open_calls(app) == [] and popup(app, "camera") is None


@pytest.mark.parametrize("loop", ["monitor_mic", "monitor_camera"])
def test_paused_loops_do_not_check_the_hardware(dash, loop):
    app = dash.app
    app.is_paused = True
    run_loop(dash, getattr(app, loop), 5)
    assert app.mic_source.calls == app.camera_source.calls == 0


# --- timing rules at their boundaries ---

def show_camera(app):
    app.show_alert("camera", "Camera Disconnected", "test")
    app.root.flush()


def test_a_popup_stays_up_for_the_minimum_display_time(dash):
    app = dash.app
    show_camera(app)

    dash.clock.advance(ALERT_MIN_DISPLAY_SECONDS - 0.5)
    app.hide_alert("camera")
    app.root.flush()
    assert popup(app, "camera") is not None and open_calls(app) != []  # too soon: popup and call stay

    dash.clock.advance(0.5)  # exactly the minimum
    app.hide_alert("camera")
    app.root.flush()
    assert popup(app, "camera") is None and open_calls(app) == []


def test_the_same_fault_cannot_pop_up_again_during_the_cooldown(dash):
    app = dash.app
    show_camera(app)
    dash.clock.advance(ALERT_MIN_DISPLAY_SECONDS)
    app.hide_alert("camera")
    app.root.flush()

    dash.clock.advance(ALERT_COOLDOWN_SECONDS - 0.5)
    show_camera(app)
    assert popup(app, "camera") is None
    assert open_calls(app) == [("CAM-LOCAL", "CAMERA_FAILURE")]  # the call is still opened, no fault goes unrecorded

    dash.clock.advance(0.5)  # cooldown over
    show_camera(app)
    assert popup(app, "camera") is not None


def test_at_most_one_github_issue_per_fault_within_the_cooldown(dash, logged):
    app = dash.app

    def new_camera_fault():
        app.show_alert("camera", "Camera Disconnected", "test")
        app.resolve_maintenance_call("camera")  # cleared, so the next fault is a new call

    new_camera_fault()
    dash.clock.advance(GITHUB_ISSUE_COOLDOWN_SECONDS - 1)
    new_camera_fault()
    assert len(dash.github.posted) == 1
    assert "GitHub issue for camera skipped" in logged()

    dash.clock.advance(1)  # exactly the cooldown
    new_camera_fault()
    assert len(dash.github.posted) == 2


def test_an_ongoing_fault_reuses_its_call_and_issue(dash):
    app = dash.app
    for _ in range(3):
        show_camera(app)
    assert len(app.maintenance_desk.requests) == 1 and len(dash.github.posted) == 1


def test_the_decrypted_token_is_sent_to_github(dash):
    show_camera(dash.app)
    assert dash.app.github_token == "test-token"
    assert dash.github.posted[0].token == "Bearer test-token"


def test_alerts_still_show_when_no_technician_can_take_the_call(dash, logged):
    app = dash.build(technicians={})
    app.show_alert("camera", "Camera Disconnected", "test")
    app.root.flush()

    assert "Could not open a maintenance call for camera" in logged()
    assert popup(app, "camera") is not None and len(dash.github.posted) == 1


# --- quiet-period watchdog ---

def test_watchdog_forces_a_fault_after_the_quiet_period(dash):
    app = dash.app
    run_loop(dash, app.keep_room_busy, MAX_QUIET_SECONDS - 1)
    assert app.mic_source.forced + app.camera_source.forced == 0

    run_loop(dash, app.keep_room_busy, 1)  # exactly MAX_QUIET_SECONDS without a popup
    assert app.mic_source.forced + app.camera_source.forced == 1


def show(app, key):
    app.show_alert(key, f"{key} fault", "test")
    app.root.flush()


needs_two_slots = pytest.mark.skipif(MAX_VISIBLE_ALERTS < 2, reason="needs room for two popups at once")


@needs_two_slots
def test_watchdog_only_forces_a_device_free_to_show_a_popup(dash):
    app = dash.app
    show(app, "mic_conn")  # the mic already has a popup
    run_loop(dash, app.keep_room_busy, MAX_QUIET_SECONDS)
    assert (app.mic_source.forced, app.camera_source.forced) == (0, 1)


def test_watchdog_waits_while_every_popup_slot_is_taken(dash):
    # every slot is taken by other faults; the mic is free but no popup could appear
    others = ["camera", "mic_silence"]
    if MAX_VISIBLE_ALERTS > len(others):
        pytest.skip("needs more fault types than popup slots to fill every slot")
    app = dash.app
    for key in others[:MAX_VISIBLE_ALERTS]:
        show(app, key)
    run_loop(dash, app.keep_room_busy, MAX_QUIET_SECONDS + 5)
    assert app.mic_source.forced + app.camera_source.forced == 0


@needs_two_slots
def test_watchdog_waits_for_the_cooldown_after_popups_close(dash):
    # both popups stayed up for the whole quiet period, then closed: the watchdog
    # must wait out the cooldown before forcing a new fault
    app = dash.app
    show(app, "camera")
    show(app, "mic_conn")
    dash.clock.advance(MAX_QUIET_SECONDS)
    app.hide_alert("camera")
    app.hide_alert("mic_conn")
    app.root.flush()

    run_loop(dash, app.keep_room_busy, ALERT_COOLDOWN_SECONDS - 1)
    assert app.mic_source.forced + app.camera_source.forced == 0
    run_loop(dash, app.keep_room_busy, 1)  # the cooldown is over
    assert app.mic_source.forced + app.camera_source.forced == 1


def test_a_break_does_not_count_as_quiet_time(dash):
    app = dash.app
    app.is_paused = True
    run_loop(dash, app.keep_room_busy, MAX_QUIET_SECONDS * 2)
    app.is_paused = False
    run_loop(dash, app.keep_room_busy, MAX_QUIET_SECONDS - 1)
    assert app.mic_source.forced + app.camera_source.forced == 0


# --- pause / resume ---

def test_pause_hides_popups_but_keeps_calls_open(dash):
    app = dash.app
    show_camera(app)
    app.toggle_pause()
    app.root.flush()

    assert popup(app, "camera") is None
    assert open_calls(app) == [("CAM-LOCAL", "CAMERA_FAILURE")]
    assert app.status_lbl.options["text"] == "Monitoring Paused"
    assert app.pause_btn.options["text"] == "Resume Monitoring"


def test_no_new_alerts_while_paused(dash):
    app = dash.app
    app.toggle_pause()
    show_camera(app)
    assert open_calls(app) == [] and dash.github.posted == []


def test_pause_before_a_popup_is_drawn_cancels_it(dash):
    app = dash.app
    app.show_alert("camera", "Camera Disconnected", "test")  # popup reserved, not yet drawn
    app.toggle_pause()
    app.root.flush()

    assert FakeToplevel.created == []
    assert app.active_alerts["camera"] is None

    app.toggle_pause()  # its slot was freed: the next popup goes to the top slot
    show_camera(app)
    (drawn,) = FakeToplevel.created
    assert drawn.spec.endswith("+40")


def test_resume_restarts_the_silence_timer(dash):
    app = dash.app
    app.toggle_pause()
    dash.clock.advance(100)
    app.toggle_pause()

    assert app.silence_start == dash.clock.now
    assert app.status_lbl.options["text"] == "Monitoring Active"


# --- shutdown ---

def test_after_closing_alerts_are_ignored(dash):
    app = dash.app
    app.stop()
    assert app.running is False and app.root.destroyed

    app.show_alert("camera", "Camera Disconnected", "test")
    app.hide_alert("camera")
    assert open_calls(app) == [] and dash.github.posted == []


def test_a_ui_error_while_running_is_not_hidden(dash):
    app = dash.app
    app.root.destroyed = True  # the window broke while monitoring is still on
    with pytest.raises(FakeTclError):
        app.show_alert("camera", "Camera Disconnected", "test")


def test_a_ui_error_during_shutdown_is_ignored(dash):
    app = dash.app
    app.root.destroyed = True
    app.running = True

    def closing_after_check():
        app.running = False  # stop() runs between the running check and after()
        raise FakeTclError("application has been destroyed")

    app.root.after = lambda delay, callback: closing_after_check()
    app.show_alert("camera", "Camera Disconnected", "test")  # must not raise


# --- calls left open by a previous run ---

def test_leftover_calls_are_adopted_and_duplicates_closed(dash, make_call, write_jsonl, logged):
    this_machine = {"workstation_id": COMPUTER_NAME, "component_id": "CAM-LOCAL"}
    log_path = write_jsonl("leftover.jsonl",
                           make_call(1, "CAMERA_FAILURE", "T1", "IN_PROGRESS", "09:00", **this_machine),
                           make_call(2, "CAMERA_FAILURE", "T1", "IN_PROGRESS", "10:00", **this_machine))

    app = dash.build(log_path=log_path)
    assert app.open_requests.get("camera").request_id == "MR-0001"  # the oldest is kept
    assert "Closed duplicate maintenance call MR-0002" in logged()
    assert [r.request_id for r in app.maintenance_desk.open_requests()] == ["MR-0001"]

    run_loop(dash, app.monitor_camera, 1)  # camera healthy: the adopted call is resolved
    assert app.maintenance_desk.open_requests() == []
