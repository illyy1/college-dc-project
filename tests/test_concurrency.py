"""Concurrency validations: the asynchronous collection returns accurate,
correctly ordered results and really runs its sources at the same time, and
every lock protects the shared state it guards. The lock tests are built to
be deterministic: they hold a lock and check that callers wait for it, or
slow down a critical section so that unprotected code would fail every time."""

import asyncio
import contextlib
import gc
import json
import threading
import time
from collections import Counter

import pytest

from hardware_monitor import concurrency
from hardware_monitor.concurrency import (
    LockedAlertLog,
    fetch_statuses_concurrent,
    fetch_statuses_sequential,
    gather_overview_concurrent,
    gather_overview_sequential,
)
from hardware_monitor.config import MAX_VISIBLE_ALERTS
from hardware_monitor.repository import load_maintenance_history

from conftest import TEST_FLEET_PATH, run_together
from fake_tk import FakeToplevel


@pytest.fixture
def no_latency(monkeypatch):
    monkeypatch.setattr(concurrency, "SOURCE_LATENCY_SECONDS", 0)


# --- asynchronous collection: accuracy ---

def test_status_snapshots_match_the_model(workstations):
    snapshots = asyncio.run(fetch_statuses_concurrent(workstations))

    for snapshot, workstation in zip(snapshots, workstations, strict=True):
        assert snapshot["workstation_id"] == workstation.workstation_id
        assert snapshot["name"] == workstation.name
        assert snapshot["component_statuses"] == [
            {"component_id": c.component_id, "status": c.connection_status} for c in workstation.components
        ]


def test_results_keep_input_order_when_tasks_finish_out_of_order(workstations, monkeypatch):
    finished = []
    real_fetch = concurrency._fetch_workstation_status

    async def first_is_slowest(workstation):
        position = workstations.index(workstation)
        await asyncio.sleep(0.01 * (len(workstations) - position))
        finished.append(workstation.workstation_id)
        return await real_fetch(workstation)

    monkeypatch.setattr(concurrency, "_fetch_workstation_status", first_is_slowest)
    snapshots = asyncio.run(fetch_statuses_concurrent(workstations))

    input_order = [w.workstation_id for w in workstations]
    assert finished == input_order[::-1]                            # they really finished last-to-first
    assert [s["workstation_id"] for s in snapshots] == input_order  # but come back in input order
    assert snapshots == asyncio.run(fetch_statuses_sequential(workstations))


def test_overview_matches_the_data_counted_independently(no_latency, workstations, technicians, technicians_file,
                                                         make_call, write_jsonl):
    log_path = write_jsonl(
        "log.jsonl",
        make_call(1, "CAMERA_FAILURE", "T1", "IN_PROGRESS"),
        make_call(2, "CAMERA_FAILURE", "T2", "IN_PROGRESS"),
        make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED"),   # later line wins
        make_call(3, "CAMERA_FAILURE", "T3", "IN_PROGRESS"),
    )

    overview = asyncio.run(gather_overview_concurrent(TEST_FLEET_PATH, technicians_file, str(log_path)))

    statuses = Counter(c.connection_status for w in workstations for c in w.components)
    assert overview["fleet"] == {"workstations": len(workstations), "components_by_status": dict(statuses)}
    assert overview["technicians"] == [tech.name for tech in technicians.values()]
    history = load_maintenance_history(str(log_path))
    assert overview["maintenance"] == {"total_calls": len(history), "open_calls": ["MR-0002", "MR-0003"]}


def test_repeated_concurrent_runs_give_identical_results(no_latency, technicians_file):
    sources = (TEST_FLEET_PATH, technicians_file, "missing.jsonl")
    first = asyncio.run(gather_overview_concurrent(*sources))
    assert all(asyncio.run(gather_overview_concurrent(*sources)) == first for _ in range(10))


# --- asynchronous collection: really concurrent ---

@pytest.fixture
def traced_sources(monkeypatch):
    """Spies on the three real sources: each records when it starts and ends,
    then runs the real source unchanged."""
    events = []
    monkeypatch.setattr(concurrency, "SOURCE_LATENCY_SECONDS", 0.05)

    def traced(name, real_source):
        async def source(path):
            events.append(("start", name))
            result = await real_source(path)
            events.append(("end", name))
            return result
        return source

    for name in ("fleet_status", "technician_roster", "maintenance_summary"):
        attribute = f"_gather_{name}"  # a name built at runtime: PyCharm would read the f-string literally
        real_source = getattr(concurrency, attribute)
        monkeypatch.setattr(concurrency, attribute, traced(name, real_source))
    return events


def test_all_three_sources_run_at_the_same_time(traced_sources, tmp_path, technicians_file):
    sources = (TEST_FLEET_PATH, technicians_file, str(tmp_path / "log.jsonl"))
    overview = asyncio.run(gather_overview_concurrent(*sources))

    assert [kind for kind, _ in traced_sources[:3]] == ["start"] * 3  # all started before any finished
    assert overview == asyncio.run(gather_overview_sequential(*sources))  # and the real data came back


def test_the_sequential_version_runs_one_source_at_a_time(traced_sources, tmp_path, technicians_file):
    asyncio.run(gather_overview_sequential(TEST_FLEET_PATH, technicians_file, str(tmp_path / "log.jsonl")))
    assert [kind for kind, _ in traced_sources] == ["start", "end"] * 3


def test_workstation_statuses_are_fetched_concurrently(workstations, monkeypatch):
    in_flight, peak = 0, 0
    real_fetch = concurrency._fetch_workstation_status

    async def counted(workstation):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        result = await real_fetch(workstation)
        in_flight -= 1
        return result

    monkeypatch.setattr(concurrency, "_fetch_workstation_status", counted)
    asyncio.run(fetch_statuses_concurrent(workstations))
    assert peak == len(workstations)

    peak = 0
    asyncio.run(fetch_statuses_sequential(workstations))
    assert peak == 1


def test_blocking_file_reads_do_not_freeze_the_event_loop(no_latency, monkeypatch, tmp_path, technicians_file):
    real_load = concurrency.load_workstations_from_jsonl

    def slow_disk(path):
        time.sleep(0.3)  # blocking, like a slow network drive
        return real_load(path)

    monkeypatch.setattr("hardware_monitor.concurrency.load_workstations_from_jsonl", slow_disk)

    async def scenario():
        ticks, done = 0, False

        async def heartbeat():
            nonlocal ticks
            while not done:
                ticks += 1
                await asyncio.sleep(0.01)

        beat = asyncio.create_task(heartbeat())
        await gather_overview_concurrent(TEST_FLEET_PATH, technicians_file, str(tmp_path / "log.jsonl"))
        done = True
        await beat
        return ticks

    # if the read ran on the event loop, the heartbeat would stall for the whole 0.3 s
    assert asyncio.run(scenario()) >= 10


def test_a_failing_source_leaves_no_unhandled_task_errors(no_latency, tmp_path):
    async def scenario():
        reported = []
        asyncio.get_running_loop().set_exception_handler(lambda loop, context: reported.append(context))
        with pytest.raises(FileNotFoundError, match="no_fleet.jsonl|no_techs.jsonl"):
            # two of the three sources fail
            await gather_overview_concurrent(str(tmp_path / "no_fleet.jsonl"), str(tmp_path / "no_techs.jsonl"),
                                             str(tmp_path / "log.jsonl"))
        await asyncio.sleep(0.05)  # let the remaining tasks finish
        gc.collect()               # "exception was never retrieved" is reported when a task is collected
        return reported

    assert asyncio.run(scenario()) == []


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


def test_locked_alert_log_methods_wait_for_its_lock(alerts):
    alert_log = LockedAlertLog()
    assert waits_for_lock(alert_log._lock, lambda: alert_log.append_batch([alerts.camera()]))
    assert waits_for_lock(alert_log._lock, alert_log.snapshot)


def test_a_free_lock_does_not_block():
    # control for the helper above: with nothing holding the lock, the action finishes at once
    alert_log = LockedAlertLog()
    finished = threading.Event()

    def take_snapshot():
        alert_log.snapshot()
        finished.set()

    threading.Thread(target=take_snapshot).start()
    assert finished.wait(0.2)


# --- locks: shared resources stay consistent under contention ---

class SlowList(list):
    """A list whose extend lets other threads run between items, like any
    multi-step update; only a lock keeps a batch together."""

    def extend(self, items):
        for item in items:
            self.append(item)
            time.sleep(0.0005)


def batches_are_contiguous(log, batches):
    for batch in batches:
        start = log.index(batch[0])
        if log[start:start + len(batch)] != batch:
            return False
    return True


def fill_from_threads(alert_log, batches):
    remaining = list(batches)
    run_together(lambda: alert_log.append_batch(remaining.pop()), count=len(batches))


@pytest.fixture
def make_batches(alerts):
    """Builds `count` batches of `size` fresh alerts each, one component per batch."""
    def build(count=6, size=20):
        return [[alerts.make(component_id=f"C{b}") for _ in range(size)] for b in range(count)]
    return build


def test_lock_keeps_each_batch_together(make_batches):
    alert_log = LockedAlertLog()
    alert_log._alerts = SlowList()
    batches = make_batches()
    fill_from_threads(alert_log, batches)

    log = alert_log.snapshot()
    assert len(log) == 120 and batches_are_contiguous(log, batches)


def test_without_the_lock_batches_get_mixed_up(make_batches):
    # control experiment: the same workload with the lock removed shows what it prevents
    alert_log = LockedAlertLog()
    alert_log._alerts = SlowList()
    alert_log._lock = contextlib.nullcontext()
    batches = make_batches()
    fill_from_threads(alert_log, batches)

    assert not batches_are_contiguous(alert_log.snapshot(), batches)


def test_snapshots_never_see_half_a_batch(make_batches):
    alert_log = LockedAlertLog()
    alert_log._alerts = SlowList()
    batches = make_batches(count=6, size=10)
    sizes, writing = [], threading.Event()

    def reader():
        while writing.is_set():
            sizes.append(len(alert_log.snapshot()))

    writing.set()
    watcher = threading.Thread(target=reader)
    watcher.start()
    fill_from_threads(alert_log, batches)
    writing.clear()
    watcher.join()

    assert sizes and all(size % 10 == 0 for size in sizes)


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
    assert waits_for_lock(app._calls_lock, lambda: app.open_maintenance_call("camera", "test"))
    assert waits_for_lock(app._calls_lock, lambda: app.resolve_maintenance_call("camera"))
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


def test_only_one_github_issue_per_fault_within_the_cooldown(dash, capsys):
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
    assert capsys.readouterr().out.count("GitHub issue for camera skipped") == new_calls - 1


def test_mixed_dashboard_operations_finish_without_deadlock(dash):
    app = dash.app

    def worker():
        for _ in range(3):
            app.show_alert("mic_conn", "Microphone Disconnected", "test")
            app.open_maintenance_call("mic_conn", "test")
            app.hide_alert("mic_conn")
            app.resolve_maintenance_call("mic_conn")

    run_together(worker)  # fails if any thread is still stuck after 30 s
