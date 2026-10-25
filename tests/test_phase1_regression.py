"""Regression tests for Phase 1 behaviour that the other test files don't
cover: iterators, the generator, the lazy pipeline, the context manager, the
abstract component base class, loader validation, the reports, and the full
demo. They guard the Phase 1 logic against later changes (concurrency, locks,
async gathering)."""

import csv
from contextlib import ExitStack
from datetime import date, timedelta
from itertools import islice

import pytest

from conftest import TEST_FLEET_PATH

from hardware_monitor import concurrency, demo
from hardware_monitor.context_managers import DiagnosticConnection
from hardware_monitor.iterators import AlertHistory, iter_disconnected_components, lazy_disconnected_pipeline
from hardware_monitor.models import Camera, HardwareComponent, MaintenanceRequest, Microphone, Technician, Workstation
from hardware_monitor.reports import (
    export_maintenance_history_csv,
    find_unknown_technician_references,
    generate_maintenance_history_report,
    generate_technician_workload_report,
    validate_and_assign_request,
)
from hardware_monitor.repository import (
    load_maintenance_history,
    load_technicians_from_jsonl,
    load_workstations_from_jsonl,
)


def years_ago(years):
    return (date.today() - timedelta(days=round(years * 365.25))).isoformat()


def tracked(fleet, inspected):
    """Yields the fleet while recording which workstations were reached."""
    for workstation in fleet:
        inspected.append(workstation.workstation_id)
        yield workstation


@pytest.fixture(autouse=True)
def ports_are_released():
    yield
    assert DiagnosticConnection.available_ports() == list(DiagnosticConnection.PORT_POOL)


# --- custom Iterable/Iterator ---

def test_alert_history_yields_only_unresolved(alerts):
    open_a, done, open_b = alerts.make(), alerts.make(resolved=True), alerts.make()
    history = AlertHistory([open_a, done, open_b])

    assert len(history) == 3
    assert list(history) == [open_a, open_b]


def test_each_iter_call_walks_the_history_independently(alerts):
    logged = [alerts.make() for _ in range(3)]
    history = AlertHistory(logged)
    first, second = iter(history), iter(history)

    assert next(first) is logged[0]
    assert next(first) is logged[1]
    assert next(second) is logged[0]  # not moved by the first iterator
    assert list(first) == [logged[2]]
    with pytest.raises(StopIteration):
        next(first)
    assert list(second) == logged[1:]


# --- generator ---

def test_generator_yields_disconnected_components_lazily(workstations):
    inspected = []
    disconnected = iter_disconnected_components(tracked(workstations, inspected))
    assert inspected == []  # nothing runs until the first next()

    assert next(disconnected).component_id == "CAM-104"
    assert inspected == ["WS-101", "WS-102", "WS-103", "WS-104"]
    assert [c.component_id for c in disconnected] == ["CAM-107", "MIC-107"]
    assert list(disconnected) == []  # exhausted after one pass


# --- 3-step lazy pipeline ---

@pytest.fixture
def aging_fleet():
    fleet = []
    for number, (status, age) in enumerate([
        ("DISCONNECTED", 5),  # old and disconnected -> selected
        ("ACTIVE", 6),        # old but connected
        ("DISCONNECTED", 1),  # disconnected but new
        ("DISCONNECTED", 4),  # selected
        ("DISCONNECTED", 8),  # selected
    ], start=1):
        workstation = Workstation(f"WS-{number}", f"Desk {number}")
        workstation.add_component(Camera(f"CAM-{number}", "Cam", f"WS-{number}",
                                          connection_status=status, installed_on=years_ago(age)))
        fleet.append(workstation)
    return fleet


def test_lazy_pipeline_filters_disconnected_and_old(aging_fleet):
    assert list(lazy_disconnected_pipeline(aging_fleet, min_age_years=3)) == ["CAM-1", "CAM-4", "CAM-5"]


def test_lazy_pipeline_stops_right_after_the_limit(aging_fleet):
    inspected = []
    pipeline = lazy_disconnected_pipeline(tracked(aging_fleet, inspected), min_age_years=3)

    assert list(islice(pipeline, 2)) == ["CAM-1", "CAM-4"]
    assert inspected == ["WS-1", "WS-2", "WS-3", "WS-4"]  # WS-5 never reached


def test_component_age():
    assert Camera("C1", "Cam", "WS-1").age_years is None
    assert Camera("C2", "Cam", "WS-1", installed_on=years_ago(2)).age_years == pytest.approx(2, abs=0.01)


# --- context manager ---

def test_diagnostic_connection_locks_and_restores_status():
    camera = Camera("C1", "Cam", "WS-1")
    connection = DiagnosticConnection(camera, verbose=False)

    with connection as tested:
        assert tested is camera
        assert camera.connection_status == "TESTING"
        assert connection.port == "DIAG-1"
        assert "DIAG-1" not in DiagnosticConnection.available_ports()

    assert camera.connection_status == "ACTIVE"
    assert connection.port is None


def test_diagnostic_connection_keeps_the_diagnostics_result():
    camera = Camera("C1", "Cam", "WS-1", resolution="0x0")
    with DiagnosticConnection(camera, verbose=False) as tested:
        assert tested.run_diagnostics() is False
    assert camera.connection_status == "DISCONNECTED"


def test_diagnostic_connection_marks_error_and_reraises():
    camera = Camera("C1", "Cam", "WS-1")
    with pytest.raises(RuntimeError, match="cable pulled"):
        with DiagnosticConnection(camera, verbose=False):
            raise RuntimeError("cable pulled")
    assert camera.connection_status == "ERROR"


def test_diagnostic_connection_refuses_when_all_ports_are_busy():
    cameras = [Camera(f"C{n}", "Cam", "WS-1") for n in range(4)]
    with ExitStack() as stack:
        for camera in cameras[:3]:
            stack.enter_context(DiagnosticConnection(camera, verbose=False))
        assert DiagnosticConnection.available_ports() == []
        with pytest.raises(RuntimeError, match="No free diagnostic port"):
            with DiagnosticConnection(cameras[3], verbose=False):
                pass
    assert cameras[3].connection_status == "ACTIVE"


# --- abstract base class, factories and polymorphism ---

def test_hardware_component_cannot_be_instantiated():
    # the wording differs between Python versions; both name the two missing methods
    with pytest.raises(TypeError, match=r"abstract methods.*alert_priority.*run_diagnostics"):
        HardwareComponent("X1", "Thing", "WS-1")


def test_from_dict_builds_the_matching_subclass():
    camera = HardwareComponent.from_dict(
        {"component_type": "camera", "component_id": "C1", "workstation_id": "WS-1", "resolution": "1280x720"})
    microphone = HardwareComponent.from_dict(
        {"component_type": "Microphone", "component_id": "M1", "workstation_id": "WS-1", "sample_rate": 16000})

    assert isinstance(camera, Camera) and camera.resolution == "1280x720"
    assert isinstance(microphone, Microphone) and microphone.sample_rate == 16000
    with pytest.raises(ValueError, match="Unknown component_type"):
        HardwareComponent.from_dict({"component_type": "Speaker", "component_id": "S1", "workstation_id": "WS-1"})


@pytest.mark.parametrize("resolution, parsed", [
    ("1920x1080", (1920, 1080)),
    ("1280X720", (1280, 720)),
    ("0x0", None),
    ("garbage", None),
    (None, None),
], ids=["full-hd", "uppercase-x", "zero-size", "not-a-resolution", "none"])
def test_parse_resolution(resolution, parsed):
    assert Camera.parse_resolution(resolution) == parsed


def test_alert_priority_ranks_video_above_audio_and_lost_above_misconfigured():
    assert Camera("C1", "Cam", "WS-1").alert_priority() == 1
    assert Camera("C2", "Cam", "WS-1", resolution="0x0").alert_priority() == 2
    assert Microphone("M1", "Mic", "WS-1").alert_priority() == 3
    assert Microphone("M2", "Mic", "WS-1", sample_rate=4000).alert_priority() == 4


# --- loader validation ---

GOOD_LINE = {"component_type": "Camera", "component_id": "CAM-1", "workstation_id": "WS-1",
             "workstation_name": "Desk", "location": "Room 1"}


@pytest.mark.parametrize("bad_line, message", [
    ("{not json", "invalid JSON"),
    ({**GOOD_LINE, "component_id": "CAM-2", "workstation_id": None}, "missing workstation_id"),
    ({**GOOD_LINE, "component_id": "CAM-2", "component_type": "Speaker"}, "Unknown component_type"),
    ({**GOOD_LINE, "component_id": "CAM-2", "connection_status": "MELTED"}, "Invalid connection status"),
    ({**GOOD_LINE, "component_id": "CAM-2", "installed_on": "yesterday"}, "CAM-2"),
    (GOOD_LINE, "duplicate component_id 'CAM-1'"),
    ({**GOOD_LINE, "component_id": "CAM-2", "location": "Room 2"}, "different name/location"),
], ids=["invalid-json", "null-workstation", "unknown-type", "illegal-status", "bad-date", "duplicate-id",
        "contradicting-location"])
def test_workstation_loader_rejects_bad_records_with_line_number(write_jsonl, bad_line, message):
    path = write_jsonl("data.jsonl", GOOD_LINE, bad_line)
    with pytest.raises(ValueError, match="line 2") as error:
        load_workstations_from_jsonl(path)
    assert message in str(error.value)


def test_technician_loader_rejects_a_duplicate_id(write_jsonl):
    tech = {"tech_id": "T1", "name": "Dana"}
    with pytest.raises(ValueError, match="line 2: duplicate tech_id 'T1'"):
        load_technicians_from_jsonl(write_jsonl("dup.jsonl", tech, tech))


def test_an_old_technician_file_still_loads(write_jsonl):
    # technicians used to have a specialty and a workstation list; a file that
    # still has them loads, and both are ignored
    old = {"tech_id": "T1", "name": "Dana", "specialty": "Camera", "workstation_ids": ["WS-101"]}
    loaded = load_technicians_from_jsonl(write_jsonl("old.jsonl", old))
    assert loaded["T1"].name == "Dana"
    assert not hasattr(loaded["T1"], "specialty") and not hasattr(loaded["T1"], "workstation_ids")


def test_maintenance_history_keeps_latest_state_and_skips_corrupt_lines(make_call, write_jsonl, tmp_path):
    path = write_jsonl("log.jsonl",
                       make_call(10, "CAMERA_FAILURE", "T1", "IN_PROGRESS"),
                       make_call(2, "CAMERA_FAILURE", "T2", "IN_PROGRESS"),
                       make_call(10, "CAMERA_FAILURE", "T1", "RESOLVED"),
                       fragment='{"request_id": "MR-0011", "sta')  # half-written last line
    history = load_maintenance_history(path)

    assert [(r["request_id"], r["status"]) for r in history] == [("MR-0002", "IN_PROGRESS"), ("MR-0010", "RESOLVED")]
    assert load_maintenance_history(str(tmp_path / "missing.jsonl")) == []


# --- reports ---

def test_unknown_technician_references_are_reported(reports_log, technicians):
    unknown = find_unknown_technician_references(load_maintenance_history(reports_log), technicians)
    assert [r["request_id"] for r in unknown] == ["MR-0002"]


def test_history_csv_export(reports_log, tmp_path):
    csv_path = export_maintenance_history_csv(reports_log, str(tmp_path / "reports" / "out.csv"), verbose=False)

    with open(csv_path, encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))
    assert [(r["request_id"], r["status"], r["assigned_tech_name"]) for r in rows] == [
        ("MR-0001", "RESOLVED", "Dana Levi"),
        ("MR-0002", "IN_PROGRESS", ""),
    ]


def test_history_report_lists_every_call(reports_log, tmp_path, capsys):
    history = generate_maintenance_history_report(reports_log)
    output = capsys.readouterr().out

    assert len(history) == 2
    assert "MR-0001" in output and "MR-0002" in output and "Dana Levi: 1" in output
    assert generate_maintenance_history_report(str(tmp_path / "empty.jsonl")) == []


def test_assignment_validation_and_reassignment(technicians, capsys):
    request = MaintenanceRequest("MR-0001", alert_id=1, priority=1)
    validate_and_assign_request(request, "T1", technicians)
    validate_and_assign_request(request, "T3", technicians)

    assert request.assigned_tech_id == "T3"
    assert request not in technicians["T1"].assigned_requests
    assert technicians["T3"].assigned_requests == [request]
    with pytest.raises(ValueError, match="'T99' אינו קיים"):  # "'T99' does not exist"
        validate_and_assign_request(request, "T99", technicians)
    assert request.assigned_tech_id == "T3"  # the failed reassignment changed nothing

    capsys.readouterr()
    generate_technician_workload_report(technicians)
    assert "MR-0001" in capsys.readouterr().out

    request.resolve()
    with pytest.raises(ValueError, match="MR-0001 כבר טופלה"):  # "MR-0001 was already handled"
        validate_and_assign_request(request, "T1", technicians)
    generate_technician_workload_report(technicians)
    assert "MR-0001" not in capsys.readouterr().out  # resolved calls leave the workload report


def test_technician_str_counts_only_open_calls():
    tech = Technician("T1", "Dana")
    done = MaintenanceRequest("MR-1", 1, 1)
    done.resolve()
    tech.assigned_requests += [MaintenanceRequest("MR-2", 2, 1), done]
    assert str(tech).endswith("1")


# --- the whole demo, end to end ---

def test_full_demo_runs_from_start_to_finish(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(demo, "SAMPLE_DATA_PATH", TEST_FLEET_PATH)  # the exact faults asserted below
    monkeypatch.setattr(demo, "DEMO_LOG_PATH", str(tmp_path / "demo_history.jsonl"))
    monkeypatch.setattr(demo, "DEMO_MAINTENANCE_LOG_PATH", str(tmp_path / "demo_maintenance.jsonl"))
    monkeypatch.setattr(concurrency, "SOURCE_LATENCY_SECONDS", 0)

    demo.run_demo()
    output = capsys.readouterr().out

    for step in range(1, 12):
        assert f"STEP {step} " in output
    assert "Logged 5 alert(s) from failed diagnostics." in output
    assert "Same overview either way: True" in output
    assert "Maintenance: 4 call(s)" in output
    assert "unknown workstation" not in output  # technicians no longer list workstations
    assert "with an unknown technician" in output  # the log <-> technicians check remains
