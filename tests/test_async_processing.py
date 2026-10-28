"""The asynchronous collection (async_processing) returns accurate,
correctly ordered results and really runs its sources at the same time."""

import asyncio
import gc
import time
from collections import Counter

import pytest

from hardware_monitor import async_processing
from hardware_monitor.async_processing import (
    fetch_statuses_concurrent,
    fetch_statuses_sequential,
    gather_overview_concurrent,
    gather_overview_sequential,
)
from hardware_monitor.repository import load_maintenance_history

from conftest import TEST_FLEET_PATH


@pytest.fixture
def no_latency(monkeypatch):
    monkeypatch.setattr(async_processing, "SOURCE_LATENCY_SECONDS", 0)


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
    real_fetch = async_processing._fetch_workstation_status

    async def first_is_slowest(workstation):
        position = workstations.index(workstation)
        await asyncio.sleep(0.01 * (len(workstations) - position))
        finished.append(workstation.workstation_id)
        return await real_fetch(workstation)

    monkeypatch.setattr(async_processing, "_fetch_workstation_status", first_is_slowest)
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
    monkeypatch.setattr(async_processing, "SOURCE_LATENCY_SECONDS", 0.05)

    def traced(name, real_source):
        async def source(path):
            events.append(("start", name))
            result = await real_source(path)
            events.append(("end", name))
            return result
        return source

    for name in ("fleet_status", "technician_roster", "maintenance_summary"):
        attribute = f"_gather_{name}"  # a name built at runtime: PyCharm would read the f-string literally
        real_source = getattr(async_processing, attribute)
        monkeypatch.setattr(async_processing, attribute, traced(name, real_source))
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
    real_fetch = async_processing._fetch_workstation_status

    async def counted(workstation):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        result = await real_fetch(workstation)
        in_flight -= 1
        return result

    monkeypatch.setattr(async_processing, "_fetch_workstation_status", counted)
    asyncio.run(fetch_statuses_concurrent(workstations))
    assert peak == len(workstations)

    peak = 0
    asyncio.run(fetch_statuses_sequential(workstations))
    assert peak == 1


def test_blocking_file_reads_do_not_freeze_the_event_loop(no_latency, monkeypatch, tmp_path, technicians_file):
    real_load = async_processing.load_workstations_from_jsonl

    def slow_disk(path):
        time.sleep(0.3)  # blocking, like a slow network drive
        return real_load(path)

    monkeypatch.setattr("hardware_monitor.async_processing.load_workstations_from_jsonl", slow_disk)

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
