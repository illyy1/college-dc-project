import csv
import json

import pytest

from hardware_monitor.maintenance import specialty_for_error_type
from hardware_monitor.models import Technician
from hardware_monitor.reports import find_unknown_workstation_references


@pytest.fixture
def desk(make_desk):
    """This file's desk also writes the CSV report."""
    return make_desk(csv_report=True)


@pytest.fixture
def ws104_team():
    """Two camera specialists and a generalist, all covering WS-104."""
    room = ["WS-104"]
    return {
        "C1": Technician("C1", "Cam One", "Camera", workstation_ids=room),
        "C2": Technician("C2", "Cam Two", "Camera", workstation_ids=room),
        "G1": Technician("G1", "Generalist", "General", workstation_ids=room),
    }


@pytest.mark.parametrize("error_type, specialty", [
    ("CAMERA_FAILURE", "Camera"),
    ("microphone_disconnected", "Microphone"),
    ("MONITOR_OFF", "General"),
])
def test_specialty_for_error_type(error_type, specialty):
    assert specialty_for_error_type(error_type) == specialty


def test_technicians_load_from_data(technicians):
    assert technicians["T1"].name == "Dana Levi"
    assert {t.specialty for t in technicians.values()} >= {"Camera", "Microphone"}


def test_technician_coverage_matches_sample_data(technicians, workstations):
    assert technicians["T1"].workstation_ids == ("WS-101", "WS-102", "WS-103", "WS-104")
    assert find_unknown_workstation_references(technicians, workstations) == []


def test_dangling_workstation_reference_is_reported(technicians, workstations):
    technicians["T9"] = Technician("T9", "Ghost", "Camera", workstation_ids=["WS-101", "WS-999"])
    assert find_unknown_workstation_references(technicians, workstations) == [("T9", "WS-999")]


def test_technician_rejects_malformed_workstation_ids():
    with pytest.raises(ValueError, match="Technician 'T9': workstation_ids must be a list"):
        Technician.from_dict({"tech_id": "T9", "name": "Ghost", "specialty": "Camera", "workstation_ids": "WS-101"})


def test_calls_go_to_matching_specialist(desk, alerts):
    cam_request = desk.open_request_for_alert(alerts.camera())
    mic_request = desk.open_request_for_alert(alerts.microphone())

    assert desk.technician_for(cam_request).specialty == "Camera"
    assert desk.technician_for(mic_request).specialty == "Microphone"
    assert cam_request.status == mic_request.status == "IN_PROGRESS"
    assert (cam_request.request_id, mic_request.request_id) == ("MR-0001", "MR-0002")


def test_camera_calls_are_balanced_between_camera_technicians(desk, alerts):
    first = desk.open_request_for_alert(alerts.camera())
    second = desk.open_request_for_alert(alerts.camera())
    assert first.assigned_tech_id != second.assigned_tech_id


def test_resolved_calls_no_longer_count_as_load(desk, alerts):
    first = desk.open_request_for_alert(alerts.camera())
    second = desk.open_request_for_alert(alerts.camera())
    desk.resolve_request(first.request_id)
    third = desk.open_request_for_alert(alerts.camera())
    assert third.assigned_tech_id == first.assigned_tech_id != second.assigned_tech_id


def test_overflows_to_general_when_specialists_are_busy(make_desk, ws104_team, alerts):
    desk = make_desk(ws104_team)
    specialists = {desk.open_request_for_alert(alerts.camera()).assigned_tech_id for _ in range(2)}
    overflow = desk.open_request_for_alert(alerts.camera())
    assert specialists == {"C1", "C2"}
    assert overflow.assigned_tech_id == "G1"


def test_falls_back_to_least_busy_when_no_specialist(make_desk, alerts):
    desk = make_desk({"T9": Technician("T9", "Solo", "Camera")})
    request = desk.open_request_for_alert(alerts.microphone())
    assert request.assigned_tech_id == "T9"


def test_no_technicians_raises(make_desk, alerts):
    desk = make_desk({})
    with pytest.raises(ValueError, match="No technicians are registered"):
        desk.open_request_for_alert(alerts.camera())
    assert desk.requests == {}  # nothing half-opened


def test_resolve_unknown_request_raises(desk):
    with pytest.raises(ValueError, match="Maintenance request 'MR-9999' does not exist"):
        desk.resolve_request("MR-9999")


def test_every_change_is_logged_and_exported(desk, alerts):
    request = desk.open_request_for_alert(alerts.camera())
    desk.resolve_request(request.request_id)
    desk.resolve_request(request.request_id)  # already resolved: not logged again

    with open(desk.log_path, encoding="utf-8") as log_file:
        records = [json.loads(line) for line in log_file]
    assert [r["status"] for r in records] == ["IN_PROGRESS", "RESOLVED"]
    assert records[-1]["assigned_tech_name"] == desk.technician_for(request).name

    with open(desk.report_csv_path, encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))
    assert len(rows) == 1 and rows[0]["status"] == "RESOLVED"


def test_numbering_continues_from_existing_log(desk, make_desk, alerts):
    desk.open_request_for_alert(alerts.camera())
    desk.open_request_for_alert(alerts.microphone())

    reopened = make_desk()  # same temporary log: a restart
    assert reopened.open_request_for_alert(alerts.camera()).request_id == "MR-0003"
