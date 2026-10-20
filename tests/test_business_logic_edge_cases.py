"""Business logic and edge cases: standard operations, invalid inputs and
boundary conditions (thresholds, empty inputs, ties, restarts, concurrent
access) across the models, loaders, maintenance desk and concurrency code."""

import asyncio
import json
import threading
import time
from datetime import date, timedelta
from itertools import islice

import pytest

from hardware_monitor import concurrency, maintenance, processing
from hardware_monitor.concurrency import (
    LockedAlertLog,
    fetch_statuses_concurrent,
    fetch_statuses_sequential,
    gather_overview_concurrent,
    gather_overview_sequential,
    record_alerts_from_threads,
)
from hardware_monitor.context_managers import DiagnosticConnection
from hardware_monitor.iterators import AlertHistory, iter_disconnected_components, lazy_disconnected_pipeline
from hardware_monitor.maintenance import MaintenanceDesk
from hardware_monitor.models import Camera, HardwareComponent, MaintenanceRequest, Microphone, Technician, Workstation
from hardware_monitor.processing import Alert, AlertManager
from hardware_monitor.repository import load_technicians_from_jsonl, load_workstations_from_jsonl, request_number

from conftest import SAMPLE_DATA_PATH, TECHNICIANS_PATH


def camera_alert(ws="WS-104"):
    return Alert("CAM-104", ws, "CAMERA_FAILURE", "disconnected", priority=1)


def desk_with(technicians, tmp_path):
    return MaintenanceDesk(technicians, log_path=str(tmp_path / "log.jsonl"), report_csv_path=None)


# --- boundary conditions on thresholds ---

@pytest.mark.parametrize("sample_rate, healthy", [
    (7999, False),
    (8000, True),     # exactly MIN_SAMPLE_RATE is accepted
    (8000.0, True),
    (True, False),    # a bool is not a sample rate
    ("44100", False),
])
def test_microphone_sample_rate_threshold(sample_rate, healthy):
    assert Microphone("M1", "Mic", "WS-1", sample_rate=sample_rate).run_diagnostics() is healthy


@pytest.mark.parametrize("resolution, parsed", [
    ("1x1", (1, 1)),             # smallest usable resolution
    (" 1920 x 1080 ", (1920, 1080)),
    ("-1x5", None),
    ("1920x1080x3", None),
    ("1920x", None),
    ("x", None),
])
def test_parse_resolution_boundaries(resolution, parsed):
    assert Camera.parse_resolution(resolution) == parsed


def test_lazy_pipeline_age_boundary_is_inclusive():
    today = date.today()
    fleet = []
    for number, days_old in enumerate((1096, 1095), start=1):  # just over / just under 3 years
        workstation = Workstation(f"WS-{number}", "Desk")
        workstation.add_component(Camera(f"CAM-{number}", "Cam", f"WS-{number}", connection_status="DISCONNECTED",
                                          installed_on=(today - timedelta(days=days_old)).isoformat()))
        fleet.append(workstation)
    no_date = Workstation("WS-3", "Desk")
    no_date.add_component(Camera("CAM-3", "Cam", "WS-3", connection_status="DISCONNECTED"))
    fleet.append(no_date)

    assert list(lazy_disconnected_pipeline(fleet, min_age_years=3)) == ["CAM-1"]


def test_fault_status_fails_diagnostics_even_with_good_settings():
    assert Camera("C1", "Cam", "WS-1", connection_status="ERROR").run_diagnostics() is False
    assert Microphone("M1", "Mic", "WS-1", connection_status="MAINTENANCE").run_diagnostics() is False


def test_diagnostics_look_past_the_testing_lock():
    camera = Camera("C1", "Cam", "WS-1", connection_status="DISCONNECTED")
    with DiagnosticConnection(camera, verbose=False) as tested:
        assert tested.connection_status == "TESTING"
        assert tested.run_diagnostics() is False  # judged on DISCONNECTED, not TESTING
    assert camera.connection_status == "DISCONNECTED"


# --- empty inputs ---

def test_empty_inputs_are_handled():
    manager = AlertManager()
    assert manager.dispatch_next() is None
    assert manager.acknowledge_next() is None
    assert manager.group_by_workstation() == {}
    assert manager.unique_error_types() == set()
    assert manager.workstations_without_audio_and_video() == set()
    assert manager.sorted_by_workstation() == []

    assert len(AlertHistory([])) == 0 and list(AlertHistory([])) == []
    assert list(iter_disconnected_components([])) == []
    assert list(lazy_disconnected_pipeline([])) == []
    assert Workstation("WS-1", "Desk").run_all_diagnostics() == {}


def test_limit_zero_inspects_nothing(workstations):
    inspected = []

    def tracked():
        for workstation in workstations:
            inspected.append(workstation)
            yield workstation

    assert list(islice(lazy_disconnected_pipeline(tracked()), 0)) == []
    assert inspected == []


def test_empty_and_blank_line_files(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert load_workstations_from_jsonl(str(empty)) == []
    assert load_technicians_from_jsonl(str(empty)) == {}

    spaced = tmp_path / "spaced.jsonl"
    record = {"tech_id": "T1", "name": "Dana", "specialty": "Camera"}
    spaced.write_text("\n\n" + json.dumps(record) + "\n   \n", encoding="utf-8")
    assert list(load_technicians_from_jsonl(str(spaced))) == ["T1"]


# --- invalid inputs ---

@pytest.mark.parametrize("record, message", [
    ({"component_type": "Microphone", "component_id": "M", "workstation_id": "W", "sample_rate": True},
     "must be a number"),
    ({"component_type": "Camera", "component_id": "C", "workstation_id": "W", "camera_index": 1.5},
     "must be an integer"),
    ({"component_type": "Camera", "component_id": "C", "workstation_id": "W", "resolution": 1080},
     "must be a string"),
    ({"component_type": "Camera", "component_id": 123, "workstation_id": "W"}, "must be text"),
    ({"component_type": "Camera", "component_id": "   ", "workstation_id": "W"}, "missing component_id"),
    ({"component_type": "Camera", "component_id": "C", "workstation_id": "W", "installed_on": 20210101},
     "must be a date string"),
    ({"component_type": "Camera", "component_id": "C", "workstation_id": "W", "installed_on": "2021-02-30"},
     "Component 'C'"),
])
def test_component_records_with_bad_values_are_rejected(record, message):
    with pytest.raises(ValueError, match=message):
        HardwareComponent.from_dict(record)


def test_subclass_factory_refuses_another_type():
    with pytest.raises(ValueError, match="got a 'Microphone' record"):
        Camera.from_dict({"component_type": "Microphone", "component_id": "M", "workstation_id": "W"})


@pytest.mark.parametrize("record", [[1], "text", None])
def test_non_object_records_are_rejected(record):
    with pytest.raises(ValueError, match="must be a JSON object"):
        Workstation.from_dict(record)


def test_loader_rejects_a_json_line_that_is_not_an_object(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_text("[1, 2]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1: expected a JSON object"):
        load_workstations_from_jsonl(str(path))


@pytest.mark.parametrize("record, message", [
    ({"tech_id": "T1", "name": "   ", "specialty": "Camera"}, "missing name"),
    ({"tech_id": 7, "name": "Dana", "specialty": "Camera"}, "must be text"),
    ({"tech_id": "T1", "name": "Dana", "specialty": "Plumbing"}, "Invalid specialty"),
    ({"tech_id": "T1", "name": "Dana", "specialty": "Camera", "workstation_ids": ["WS-1", 2]}, "workstation_ids"),
])
def test_technician_records_with_bad_values_are_rejected(record, message):
    with pytest.raises(ValueError, match=message):
        Technician.from_dict(record)


def test_workstation_refuses_foreign_and_duplicate_components():
    workstation = Workstation("WS-1", "Desk")
    workstation.add_component(Camera("C1", "Cam", "WS-1"))
    with pytest.raises(ValueError, match="belongs to workstation 'WS-2'"):
        workstation.add_component(Camera("C2", "Cam", "WS-2"))
    with pytest.raises(ValueError, match="already has a component"):
        workstation.add_component(Microphone("C1", "Mic", "WS-1"))
    assert len(workstation.components) == 1


def test_statuses_are_case_insensitive_but_validated():
    request = MaintenanceRequest("MR-1", 1, 1)
    request.status = "resolved"
    assert request.status == "RESOLVED"
    with pytest.raises(ValueError):
        Camera("C1", "Cam", "WS-1", connection_status="UNKNOWN")


@pytest.mark.parametrize("request_id, number", [
    ("MR-0012", 12), ("MR-1", 1), ("weird", 0), ("MR-", 0), ("MR-x", 0), (None, 0),
])
def test_request_number_handles_unknown_formats(request_id, number):
    assert request_number(request_id) == number


# --- maintenance desk business rules ---

def test_uncovered_workstation_considers_every_technician(technicians, tmp_path):
    desk = desk_with(technicians, tmp_path)
    request = desk.open_request_for_alert(camera_alert(ws="DASHBOARD-PC"))
    assert desk.technician_for(request).specialty == "Camera"


def test_no_overflow_while_one_specialist_is_still_free(tmp_path):
    room = ["WS-104"]
    technicians = {
        "C1": Technician("C1", "Cam One", "Camera", workstation_ids=room),
        "C2": Technician("C2", "Cam Two", "Camera", workstation_ids=room),
        "G1": Technician("G1", "Generalist", "General", workstation_ids=room),
    }
    desk = desk_with(technicians, tmp_path)
    busy = desk.open_request_for_alert(camera_alert())
    recent = desk.open_request_for_alert(camera_alert())
    desk.resolve_request(recent.request_id)
    # one specialist is busy, the other is free but was assigned more recently
    # than the generalist; the free specialist must still win
    assert desk.open_request_for_alert(camera_alert()).assigned_tech_id == recent.assigned_tech_id != busy.assigned_tech_id


def test_busy_specialists_without_a_generalist_share_the_load(tmp_path):
    technicians = {tid: Technician(tid, tid, "Camera", workstation_ids=["WS-104"]) for tid in ("C1", "C2")}
    desk = desk_with(technicians, tmp_path)
    assigned = [desk.open_request_for_alert(camera_alert()).assigned_tech_id for _ in range(4)]
    assert sorted(assigned) == ["C1", "C1", "C2", "C2"]


def test_open_calls_survive_a_restart_and_resolved_ones_do_not(technicians, tmp_path):
    desk = desk_with(technicians, tmp_path)
    still_open = desk.open_request_for_alert(camera_alert())
    done = desk.open_request_for_alert(Alert("MIC-105", "WS-105", "MICROPHONE_SILENCE", "silent", priority=4))
    desk.resolve_request(done.request_id)

    fresh_technicians = load_technicians_from_jsonl(TECHNICIANS_PATH)
    restarted = MaintenanceDesk(fresh_technicians, log_path=desk.log_path, report_csv_path=None)

    assert [r.request_id for r in restarted.open_requests()] == [still_open.request_id]
    assert [r.request_id for r in restarted.find_open_requests("WS-104", "CAM-104", "CAMERA_FAILURE")] == \
        [still_open.request_id]
    assert restarted.find_open_requests("WS-104", "CAM-104", "OTHER_ERROR") == []
    owner = fresh_technicians[still_open.assigned_tech_id]
    assert [r.request_id for r in owner.assigned_requests] == [still_open.request_id]
    assert restarted.resolve_request(still_open.request_id).status == "RESOLVED"


def test_log_naming_an_unknown_technician_is_reported_not_fatal(technicians, tmp_path, capsys):
    log_path = tmp_path / "log.jsonl"
    log_path.write_text(json.dumps({"request_id": "MR-0005", "status": "IN_PROGRESS", "assigned_tech_id": "T42",
                                    "opened_at": "2026-10-01T10:00:00"}) + "\n", encoding="utf-8")
    desk = MaintenanceDesk(technicians, log_path=str(log_path), report_csv_path=None)

    assert "unknown technician 'T42'" in capsys.readouterr().out
    assert [r.request_id for r in desk.open_requests()] == ["MR-0005"]
    assert desk.open_request_for_alert(camera_alert()).request_id == "MR-0006"


# --- concurrency (Phase 2) ---

def run_in_threads(worker, count=4):
    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def test_alert_log_writes_never_overlap(monkeypatch, tmp_path):
    writing, overlaps = [], []
    real_append = processing.append_jsonl_record

    def slow_append(path, record):
        if writing:
            overlaps.append(record["alert_id"])
        writing.append(record["alert_id"])
        time.sleep(0.002)  # widens the window two unlocked writers would collide in
        real_append(path, record)
        writing.pop()

    monkeypatch.setattr(processing, "append_jsonl_record", slow_append)
    manager = AlertManager(log_path=str(tmp_path / "history.jsonl"))
    run_in_threads(lambda: [manager.log_alert(Alert("CAM-1", "WS-1", "CAMERA_FAILURE", "x")) for _ in range(10)])

    assert overlaps == []
    assert len(manager.alert_history) == 40


def test_concurrent_alert_logging_keeps_every_record(tmp_path):
    log_path = tmp_path / "history.jsonl"
    manager = AlertManager(log_path=str(log_path))

    def worker():
        for _ in range(25):
            manager.log_alert(Alert("CAM-1", "WS-1", "CAMERA_FAILURE", "x", priority=1))

    run_in_threads(worker)

    with open(log_path, encoding="utf-8") as log_file:
        ids = [json.loads(line)["alert_id"] for line in log_file]
    assert len(ids) == len(set(ids)) == 100
    assert len(manager.alert_history) == len(manager.intake_queue) == 100


def test_concurrent_maintenance_calls_get_unique_numbers(technicians, tmp_path, monkeypatch):
    class SlowRequest(MaintenanceRequest):
        def __init__(self, *args, **kwargs):
            time.sleep(0.002)  # between reading and advancing the call number
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(maintenance, "MaintenanceRequest", SlowRequest)
    desk = desk_with(technicians, tmp_path)

    def worker():
        for _ in range(10):
            desk.open_request_for_alert(camera_alert())

    run_in_threads(worker)

    assert sorted(desk.requests) == [f"MR-{n:04d}" for n in range(1, 41)]
    assert sum(len(t.assigned_requests) for t in technicians.values()) == 40


def test_threaded_batches_stay_contiguous():
    batches = [[Alert(f"C{b}", "WS-1", "CAMERA_FAILURE", "x") for _ in range(50)] for b in range(10)]
    log = record_alerts_from_threads(batches)

    assert len(log) == 500
    for batch in batches:
        start = log.index(batch[0])
        assert log[start:start + len(batch)] == batch
    assert record_alerts_from_threads([]) == []


def test_locked_alert_log_snapshot_is_a_copy():
    alert_log = LockedAlertLog()
    alert_log.append_batch([Alert("C1", "WS-1", "CAMERA_FAILURE", "x")])
    snapshot = alert_log.snapshot()
    snapshot.clear()
    assert len(alert_log.snapshot()) == 1


def test_concurrent_status_fetch_matches_sequential_and_keeps_order(workstations):
    sequential = asyncio.run(fetch_statuses_sequential(workstations))
    concurrent = asyncio.run(fetch_statuses_concurrent(workstations))

    assert concurrent == sequential
    assert [s["workstation_id"] for s in concurrent] == [w.workstation_id for w in workstations]
    assert asyncio.run(fetch_statuses_concurrent([])) == []


@pytest.fixture
def no_latency(monkeypatch):
    monkeypatch.setattr(concurrency, "SOURCE_LATENCY_SECONDS", 0)


def test_async_overview_matches_sequential(no_latency, tmp_path):
    sources = (SAMPLE_DATA_PATH, TECHNICIANS_PATH, str(tmp_path / "no_calls_yet.jsonl"))
    overview = asyncio.run(gather_overview_concurrent(*sources))

    assert overview == asyncio.run(gather_overview_sequential(*sources))
    assert overview["fleet"] == {"workstations": 8,
                                 "components_by_status": {"ACTIVE": 11, "ERROR": 2, "DISCONNECTED": 3}}
    assert overview["technicians"]["Camera"] == ["Dana Levi", "Noa Mizrahi"]
    assert overview["maintenance"] == {"total_calls": 0, "open_calls": []}  # missing log = no calls


def test_async_overview_raises_when_a_source_fails(no_latency, tmp_path):
    with pytest.raises(FileNotFoundError):
        asyncio.run(gather_overview_concurrent(str(tmp_path / "missing.jsonl"), TECHNICIANS_PATH,
                                               str(tmp_path / "log.jsonl")))
