"""Threads and shared resources (threaded_processing and every lock that
guards shared state). The tests are deterministic: they hold a lock and check
that callers wait for it, or slow down a critical section so that code
without the lock would fail every time."""

import contextlib
import json
import threading
import time
from collections import Counter

from hardware_monitor.config import MAX_VISIBLE_ALERTS
from hardware_monitor.threaded_processing import FaultCalls, report_from_threads

from conftest import run_together
from fake_tk import FakeToplevel


# --- locks: every method that touches shared state takes the lock ---

def waits_for_lock(lock, action):
    """True if action blocks while lock is held, then completes once it is released."""
    finished = threading.Event()

    def run():
        action()
        finished.set()

    thread = threading.Thread(target=run, daemon=True)
    with lock:
        thread.start()
        blocked = not finished.wait(0.2)
    return blocked and finished.wait(5)


def test_maintenance_desk_methods_wait_for_its_lock(desk, alerts):
    request = desk.open_request_for_alert(alerts.camera())

    assert waits_for_lock(desk._lock, lambda: desk.open_request_for_alert(alerts.camera()))
    assert waits_for_lock(desk._lock, lambda: desk.resolve_request(request.request_id))
    assert waits_for_lock(desk._lock, lambda: desk.update_request(request.request_id, lambda call: None))
    assert waits_for_lock(desk._lock, desk.open_requests)
    assert waits_for_lock(desk._lock, lambda: desk.find_open_requests("WS-104", "CAM-104", "CAMERA_FAILURE"))


def test_alert_manager_logging_waits_for_its_lock(tmp_path, alerts, alert_manager):
    manager = alert_manager
    assert waits_for_lock(manager._lock, lambda: manager.log_alert(alerts.camera()))


def test_fault_calls_methods_wait_for_its_lock():
    calls = FaultCalls()
    assert waits_for_lock(calls._lock, lambda: calls.open_once("camera", lambda: "MR-0001"))
    assert waits_for_lock(calls._lock, lambda: calls.get("camera"))
    assert waits_for_lock(calls._lock, lambda: calls.adopt("mic_conn", "MR-0002"))
    assert waits_for_lock(calls._lock, lambda: calls.pop("camera"))


def test_a_free_lock_does_not_block():
    # control for the helper above: with nothing holding the lock, the action finishes at once
    calls = FaultCalls()
    finished = threading.Event()

    def look():
        calls.get("camera")
        finished.set()

    threading.Thread(target=look).start()
    assert finished.wait(0.2)


# --- the shared resource: one fault, one call, however many threads report it ---

def slow_opener(desk, alerts):
    """Opens a real call on the desk, pausing first: the window between
    "this fault has no call" and "it has one" that a race needs."""
    def open_call():
        time.sleep(0.01)
        return desk.open_request_for_alert(alerts.camera())
    return open_call


def test_eight_threads_reporting_one_fault_open_one_call(desk, alerts):
    calls = FaultCalls()
    results = report_from_threads(calls, "camera", slow_opener(desk, alerts), threads=8)

    assert sum(is_new for _, is_new in results) == 1          # one approval
    assert len({call.request_id for call, _ in results}) == 1  # every thread got that call
    assert list(desk.requests) == ["MR-0001"]                  # final state: one call on the desk
    assert calls.get("camera").request_id == "MR-0001"


def test_without_the_lock_one_fault_opens_several_calls(desk, alerts):
    # control experiment: the same threads with the lock removed show what it prevents
    calls = FaultCalls()
    calls._lock = contextlib.nullcontext()
    results = report_from_threads(calls, "camera", slow_opener(desk, alerts), threads=8)

    assert sum(is_new for _, is_new in results) > 1 and len(desk.requests) > 1


def test_concurrent_calls_and_resolutions_stay_consistent(desk, technicians, slow_requests, alerts):
    slow_requests(0.001)  # inside the critical section: reading and advancing the call number

    def worker():
        for _ in range(5):
            request = desk.open_request_for_alert(alerts.camera())
            desk.open_requests()
            desk.resolve_request(request.request_id)

    run_together(worker)

    assert sorted(desk.requests) == [f"MR-{n:04d}" for n in range(1, 41)]
    assert desk.open_requests() == []
    assert sum(len(t.assigned_requests) for t in technicians.values()) == 40
    with open(desk.log_path, encoding="utf-8") as log_file:
        records = [json.loads(line) for line in log_file]
    assert Counter(r["status"] for r in records) == {"IN_PROGRESS": 40, "RESOLVED": 40}


# --- the dashboard's locks (the shared `dash` fixture: a real dashboard) ---

def drawn_popups():
    return [window for window in FakeToplevel.created if window.exists]


def test_dashboard_methods_wait_for_their_locks(dash):
    app = dash.app
    # the locks are private: proving a lock works means holding that exact lock
    assert waits_for_lock(app.open_requests._lock, lambda: app.open_maintenance_call("camera", "test"))
    assert waits_for_lock(app.open_requests._lock, lambda: app.resolve_maintenance_call("camera"))
    # a new fault: deciding whether it may open a GitHub issue takes the popup lock
    assert waits_for_lock(app._popup_lock, lambda: app.show_alert("mic_conn", "Microphone Disconnected", "test"))
    # the same fault again: no new issue, so only reserving its popup slot can take the lock
    assert waits_for_lock(app._popup_lock, lambda: app.show_alert("mic_conn", "Microphone Disconnected", "test"))
    assert waits_for_lock(app._popup_lock, lambda: app.hide_alert("mic_conn"))


def test_a_new_faults_github_issue_waits_for_the_popup_lock(dash):
    # with the lock held, a new fault may not decide to open a GitHub issue yet
    app = dash.app
    worker = threading.Thread(target=app.show_alert, args=("camera", "Camera Disconnected", "test"), daemon=True)
    with app._popup_lock:
        worker.start()
        worker.join(0.2)
        assert dash.github.posted == []  # nothing sent while the lock is held
    worker.join(5)
    assert [p.key for p in dash.github.posted] == ["camera"]


def test_a_fault_reported_by_many_threads_opens_one_call(dash, slow_requests):
    slow_requests(0.01)  # widens the check-then-open window
    results = []
    run_together(lambda: results.append(dash.app.open_maintenance_call("camera", "test")))

    assert len(dash.app.maintenance_desk.requests) == 1
    assert len({id(request) for request, _ in results}) == 1   # every thread got the same call
    assert sum(is_new for _, is_new in results) == 1           # and only one thread opened it


def test_popup_slots_are_never_shared_or_overbooked(dash):
    app = dash.app
    faults = [("camera", "Camera Disconnected"), ("mic_conn", "Microphone Disconnected"),
              ("mic_silence", "Microphone Silence")]
    run_together(lambda: [app.show_alert(key, title, "test") for key, title in faults])
    app.root.flush()  # the Tk loop draws the reserved popups

    popups = drawn_popups()
    assert len(popups) == MAX_VISIBLE_ALERTS                        # never more than the slots
    assert len({p.window_title for p in popups}) == len(popups)    # each fault at most once
    rows = sorted(int(p.spec.rsplit("+", 1)[1]) for p in popups)
    assert len(set(rows)) == len(rows)                             # each in its own place on screen


def test_only_one_github_issue_per_fault_within_the_cooldown(dash, logged):
    # threads keep opening and resolving the same fault, so new calls race each other;
    # within the cooldown only one of them may open a GitHub issue
    app = dash.app

    def worker():
        for _ in range(5):
            app.show_alert("camera", "Camera Disconnected", "test")
            app.resolve_maintenance_call("camera")

    run_together(worker)
    new_calls = len(app.maintenance_desk.requests)

    assert new_calls > 1 and len(dash.github.posted) == 1
    assert logged().count("GitHub issue for camera skipped") == new_calls - 1


def test_mixed_dashboard_operations_finish_without_deadlock(dash):
    app = dash.app

    def worker():
        for _ in range(3):
            app.show_alert("mic_conn", "Microphone Disconnected", "test")
            app.open_maintenance_call("mic_conn", "test")
            app.hide_alert("mic_conn")
            app.resolve_maintenance_call("mic_conn")

    run_together(worker)  # fails if any thread is still stuck after 30 s
