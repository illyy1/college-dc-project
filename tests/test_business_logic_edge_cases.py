"""Business logic and edge cases: standard operations, invalid inputs and
boundary conditions (thresholds, empty inputs, ties, restarts, concurrent
access) across the models, loaders, maintenance desk and concurrency code."""

import asyncio
import json
from datetime import date, timedelta
from itertools import islice

import pytest

from hardware_monitor import async_processing
from hardware_monitor.async_processing import (
    fetch_statuses_concurrent,
    gather_overview_concurrent,
    gather_overview_sequential,
)
from hardware_monitor.context_managers import DiagnosticConnection
from hardware_monitor.iterators import AlertHistory, iter_disconnected_components, lazy_disconnected_pipeline
from hardware_monitor.maintenance import MaintenanceDesk
from hardware_monitor.models import Camera, HardwareComponent, MaintenanceRequest, Microphone, Technician, Workstation
from hardware_monitor.processing import AlertManager
from hardware_monitor.repository import (
    DataFileError,
    load_technicians_from_jsonl,
    load_workstations_from_jsonl,
    request_number,
)
from hardware_monitor.threaded_processing import FaultCalls, report_from_threads

from conftest import TEST_FLEET_PATH, run_together


# --- boundary conditions on thresholds ---

def test_the_minimum_sample_rate():
    # the one place the default is pinned; the boundary tests below read it from Microphone
    assert Microphone.MIN_SAMPLE_RATE == 8000


@pytest.mark.parametrize("sample_rate, healthy", [
    (Microphone.MIN_SAMPLE_RATE - 1, False),
    (Microphone.MIN_SAMPLE_RATE, True),     # exactly the minimum is accepted
    (float(Microphone.MIN_SAMPLE_RATE), True),
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
], ids=["smallest-1x1", "spaces-around", "negative-width", "three-parts", "missing-height", "only-separator"])
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
    assert asyncio.run(fetch_statuses_concurrent([])) == []
    assert Workstation("WS-1", "Desk").run_all_diagnostics() == {}


def test_limit_zero_inspects_nothing(workstations):
    inspected = []

    def tracked():
        for workstation in workstations:
            inspected.append(workstation)
            yield workstation

    assert list(islice(lazy_disconnected_pipeline(tracked()), 0)) == []
    assert inspected == []


def test_empty_and_blank_line_files(write_jsonl):
    empty = write_jsonl("empty.jsonl")
    assert load_workstations_from_jsonl(empty) == []
    assert load_technicians_from_jsonl(empty) == {}

    spaced = write_jsonl("spaced.jsonl", "", "", {"tech_id": "T1", "name": "Dana"}, "   ")
    assert list(load_technicians_from_jsonl(spaced)) == ["T1"]


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
], ids=["bool-sample-rate", "decimal-camera-index", "numeric-resolution", "numeric-id", "blank-id",
        "numeric-install-date", "impossible-date"])
def test_component_records_with_bad_values_are_rejected(record, message):
    with pytest.raises(ValueError, match=message):
        HardwareComponent.from_dict(record)


def test_subclass_factory_refuses_another_type():
    with pytest.raises(ValueError, match="got a 'Microphone' record"):
        Camera.from_dict({"component_type": "Microphone", "component_id": "M", "workstation_id": "W"})


@pytest.mark.parametrize("record", [[1], "text", None], ids=["list", "string", "null"])
def test_non_object_records_are_rejected(record):
    with pytest.raises(ValueError, match="must be a JSON object"):
        Workstation.from_dict(record)


def test_loader_rejects_a_json_line_that_is_not_an_object(write_jsonl):
    with pytest.raises(DataFileError, match="line 1: expected a JSON object"):
        load_workstations_from_jsonl(write_jsonl("data.jsonl", "[1, 2]"))


@pytest.mark.parametrize("record, message", [
    ({"tech_id": "T1", "name": "   "}, "missing name"),
    ({"tech_id": "T1"}, "missing name"),
    ({"tech_id": 7, "name": "Dana"}, "must be text"),
], ids=["blank-name", "no-name", "numeric-id"])
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
    with pytest.raises(ValueError, match="Invalid connection status 'UNKNOWN'"):
        Camera("C1", "Cam", "WS-1", connection_status="UNKNOWN")


@pytest.mark.parametrize("request_id, number", [
    ("MR-0012", 12), ("MR-1", 1), ("weird", 0), ("MR-", 0), ("MR-x", 0), (None, 0),
])
def test_request_number_handles_unknown_formats(request_id, number):
    assert request_number(request_id) == number


# --- maintenance desk business rules ---

# (who takes a call is specified in test_maintenance.py)

def test_open_calls_survive_a_restart_and_resolved_ones_do_not(desk, alerts, technicians_file):
    still_open = desk.open_request_for_alert(alerts.camera())
    done = desk.open_request_for_alert(alerts.microphone())
    desk.resolve_request(done.request_id)

    fresh_technicians = load_technicians_from_jsonl(technicians_file)
    restarted = MaintenanceDesk(fresh_technicians, log_path=desk.log_path, report_csv_path=None)

    assert [r.request_id for r in restarted.open_requests()] == [still_open.request_id]
    assert [r.request_id for r in restarted.find_open_requests("WS-104", "CAM-104", "CAMERA_FAILURE")] == \
        [still_open.request_id]
    assert restarted.find_open_requests("WS-104", "CAM-104", "OTHER_ERROR") == []
    owner = fresh_technicians[still_open.assigned_tech_id]
    assert [r.request_id for r in owner.assigned_requests] == [still_open.request_id]
    assert restarted.resolve_request(still_open.request_id).status == "RESOLVED"


def test_log_naming_an_unknown_technician_is_reported_not_fatal(technicians, make_call, write_jsonl, logged, alerts):
    log_path = write_jsonl("log.jsonl", make_call(5, "CAMERA_FAILURE", "T42", "IN_PROGRESS", "10:00"))
    desk = MaintenanceDesk(technicians, log_path=log_path, report_csv_path=None)

    assert "unknown technician 'T42'" in logged()
    assert [r.request_id for r in desk.open_requests()] == ["MR-0005"]
    assert desk.open_request_for_alert(alerts.camera()).request_id == "MR-0006"


# --- concurrency (Phase 2) ---

def test_concurrent_alert_logging_keeps_every_record(tmp_path, alerts):
    log_path = tmp_path / "history.jsonl"
    manager = AlertManager(log_path=str(log_path))

    def worker():
        for _ in range(25):
            manager.log_alert(alerts.camera())

    run_together(worker, count=4)

    with open(log_path, encoding="utf-8") as log_file:
        ids = [json.loads(line)["alert_id"] for line in log_file]
    assert len(ids) == len(set(ids)) == 100
    assert len(manager.alert_history) == len(manager.intake_queue) == 100


def test_a_fault_whose_call_could_not_be_opened_tries_again():
    calls = FaultCalls()
    assert calls.open_once("camera", lambda: None) == (None, True)  # e.g. no technicians
    assert calls.get("camera") is None
    assert calls.open_once("camera", lambda: "MR-0001") == ("MR-0001", True)


def test_a_resolved_fault_opens_a_new_call_next_time():
    calls = FaultCalls()
    calls.open_once("camera", lambda: "MR-0001")
    assert calls.pop("camera") == "MR-0001"
    assert calls.pop("camera") is None  # nothing left to resolve
    assert calls.open_once("camera", lambda: "MR-0002") == ("MR-0002", True)


def test_one_thread_alone_opens_the_call():
    assert report_from_threads(FaultCalls(), "camera", lambda: "MR-0001", threads=1) == [("MR-0001", True)]


@pytest.fixture
def no_latency(monkeypatch):
    monkeypatch.setattr(async_processing, "SOURCE_LATENCY_SECONDS", 0)


def test_async_overview_matches_sequential(no_latency, tmp_path, technicians_file):
    sources = (TEST_FLEET_PATH, technicians_file, str(tmp_path / "no_calls_yet.jsonl"))
    overview = asyncio.run(gather_overview_concurrent(*sources))

    assert overview == asyncio.run(gather_overview_sequential(*sources))
    assert overview["fleet"] == {"workstations": 8,
                                 "components_by_status": {"ACTIVE": 11, "ERROR": 2, "DISCONNECTED": 3}}
    assert overview["technicians"] == [t.name for t in load_technicians_from_jsonl(technicians_file).values()]
    assert overview["maintenance"] == {"total_calls": 0, "open_calls": []}  # missing log = no calls
