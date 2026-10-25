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
from types import SimpleNamespace

import pytest

from hardware_monitor import concurrency, dashboard, maintenance
from hardware_monitor.concurrency import (
    LockedAlertLog,
    fetch_statuses_concurrent,
    fetch_statuses_sequential,
    gather_overview_concurrent,
    gather_overview_sequential,
)
from hardware_monitor.dashboard import HardwareMonitorApp
from hardware_monitor.models import MaintenanceRequest
from hardware_monitor.repository import load_maintenance_history

from conftest import TEST_FLEET_PATH


def run_together(worker, count=8):
    """Starts count threads at the same moment and waits for all of them."""
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


def test_overview_matches_the_data_counted_independently(no_latency, workstations, technicians, tmp_path, technicians_file):
    log_path = tmp_path / "log.jsonl"
    log_path.write_text("\n".join(json.dumps(r) for r in [
        {"request_id": "MR-0001", "status": "IN_PROGRESS"},
        {"request_id": "MR-0002", "status": "IN_PROGRESS"},
        {"request_id": "MR-0001", "status": "RESOLVED"},   # later line wins
        {"request_id": "MR-0003", "status": "IN_PROGRESS"},
    ]) + "\n", encoding="utf-8")

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
        real_source = getattr(concurrency, f"_gather_{name}")
        monkeypatch.setattr(concurrency, f"_gather_{name}", traced(name, real_source))
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

    monkeypatch.setattr(concurrency, "load_workstations_from_jsonl", slow_disk)

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
    thread = threading.Thread(target=lambda: (action(), finished.set()), daemon=True)
    with lock:
        thread.start()
        blocked = not finished.wait(0.2)
    return blocked and finished.wait(5)


def test_maintenance_desk_methods_wait_for_its_lock(desk, alerts):
    request = desk.open_request_for_alert(alerts.camera())

    assert waits_for_lock(desk._lock, lambda: desk.open_request_for_alert(alerts.camera()))
    assert waits_for_lock(desk._lock, lambda: desk.resolve_request(request.request_id))
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
    threading.Thread(target=lambda: (alert_log.snapshot(), finished.set())).start()
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


def test_concurrent_calls_and_resolutions_stay_consistent(desk, technicians, monkeypatch, alerts):
    class SlowRequest(MaintenanceRequest):
        def __init__(self, *args, **kwargs):
            time.sleep(0.001)  # inside the critical section: reading and advancing the call number
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(maintenance, "MaintenanceRequest", SlowRequest)

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


# --- the dashboard's locks ---

@pytest.fixture
def app(desk, tmp_path, alert_manager):
    """A dashboard without its Tk window: just the state its locks guard."""
    app = HardwareMonitorApp.__new__(HardwareMonitorApp)
    app.maintenance_desk = desk
    app.alert_manager = alert_manager
    app.open_requests = {}
    app._calls_lock = threading.Lock()
    app._popup_lock = threading.Lock()
    app.active_alerts = {"camera": None, "mic_conn": None, "mic_silence": None}
    app._shown_at, app._closed_at, app._popup_slots, app._last_issue_at = {}, {}, {}, {}
    app._last_popup_at = 0
    return app


def test_dashboard_methods_wait_for_their_locks(app):
    assert waits_for_lock(app._calls_lock, lambda: app.open_maintenance_call("camera", "test"))
    assert waits_for_lock(app._calls_lock, lambda: app.resolve_maintenance_call("camera"))
    assert waits_for_lock(app._popup_lock, lambda: app._reserve_popup_slot("camera"))
    assert waits_for_lock(app._popup_lock, lambda: app._claim_github_issue("camera"))


def test_a_fault_reported_by_many_threads_opens_one_call(app, monkeypatch):
    class SlowRequest(MaintenanceRequest):
        def __init__(self, *args, **kwargs):
            time.sleep(0.01)  # widens the check-then-open window
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(maintenance, "MaintenanceRequest", SlowRequest)
    results = []
    run_together(lambda: results.append(app.open_maintenance_call("camera", "test")))

    assert len(app.maintenance_desk.requests) == 1
    assert len({id(request) for request, _ in results}) == 1   # every thread got the same call
    assert sum(is_new for _, is_new in results) == 1           # and only one thread opened it


def test_popup_slots_are_never_shared_or_overbooked(app):
    keys = ["camera", "mic_conn", "mic_silence"]
    results = SimpleNamespace(granted=[])

    def worker():
        for key in keys:
            if app._reserve_popup_slot(key):
                results.granted.append(key)

    run_together(worker)

    assert sorted(results.granted) == sorted(set(results.granted))  # each fault reserved at most once
    assert len(results.granted) == dashboard.MAX_VISIBLE_ALERTS
    assert sorted(app._popup_slots.values()) == list(range(dashboard.MAX_VISIBLE_ALERTS))


def test_only_one_thread_may_open_a_github_issue_per_window(app, capsys):
    claims = []
    run_together(lambda: claims.append(app._claim_github_issue("camera")))

    assert claims.count(True) == 1 and claims.count(False) == 7
    assert capsys.readouterr().out.count("GitHub issue for camera skipped") == 7


def test_mixed_dashboard_operations_finish_without_deadlock(app):
    def worker():
        for _ in range(3):
            app.open_maintenance_call("mic_conn", "test")
            app._reserve_popup_slot("mic_conn")
            app._claim_github_issue("mic_conn")
            app.resolve_maintenance_call("mic_conn")

    run_together(worker)  # fails if any thread is still stuck after 30 s
