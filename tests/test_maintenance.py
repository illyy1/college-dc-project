import csv
import json

import pytest

from hardware_monitor.maintenance import MaintenanceDesk, specialty_for_error_type
from hardware_monitor.models import Technician
from hardware_monitor.processing import Alert


@pytest.fixture
def desk(technicians, tmp_path):
    return MaintenanceDesk(
        technicians,
        log_path=str(tmp_path / "logs" / "maintenance_requests.jsonl"),
        report_csv_path=str(tmp_path / "maintenance_report.csv"),
    )


def camera_alert():
    return Alert("CAM-104", "WS-104", "CAMERA_FAILURE", "disconnected", priority=1)


def mic_alert():
    return Alert("MIC-105", "WS-105", "MICROPHONE_SILENCE", "silent", priority=4)


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


def test_calls_go_to_matching_specialist(desk):
    cam_request = desk.open_request_for_alert(camera_alert())
    mic_request = desk.open_request_for_alert(mic_alert())

    assert desk.technician_for(cam_request).specialty == "Camera"
    assert desk.technician_for(mic_request).specialty == "Microphone"
    assert cam_request.status == mic_request.status == "IN_PROGRESS"
    assert (cam_request.request_id, mic_request.request_id) == ("MR-0001", "MR-0002")


def test_camera_calls_are_balanced_between_camera_technicians(desk):
    first = desk.open_request_for_alert(camera_alert())
    second = desk.open_request_for_alert(camera_alert())
    assert first.assigned_tech_id != second.assigned_tech_id


def test_resolved_calls_no_longer_count_as_load(desk):
    first = desk.open_request_for_alert(camera_alert())
    second = desk.open_request_for_alert(camera_alert())
    desk.resolve_request(first.request_id)
    third = desk.open_request_for_alert(camera_alert())
    assert third.assigned_tech_id == first.assigned_tech_id != second.assigned_tech_id


def test_overflows_to_general_when_specialists_are_busy(desk):
    desk.open_request_for_alert(camera_alert())
    desk.open_request_for_alert(camera_alert())
    overflow = desk.open_request_for_alert(camera_alert())
    assert desk.technician_for(overflow).specialty == "General"


def test_falls_back_to_least_busy_when_no_specialist(tmp_path):
    technicians = {"T9": Technician("T9", "Solo", "Camera")}
    desk = MaintenanceDesk(technicians, log_path=str(tmp_path / "log.jsonl"), report_csv_path=None)
    request = desk.open_request_for_alert(mic_alert())
    assert request.assigned_tech_id == "T9"


def test_no_technicians_raises(tmp_path):
    desk = MaintenanceDesk({}, log_path=str(tmp_path / "log.jsonl"), report_csv_path=None)
    with pytest.raises(ValueError):
        desk.open_request_for_alert(camera_alert())


def test_resolve_unknown_request_raises(desk):
    with pytest.raises(ValueError):
        desk.resolve_request("MR-9999")


def test_every_change_is_logged_and_exported(desk):
    request = desk.open_request_for_alert(camera_alert())
    desk.resolve_request(request.request_id)
    desk.resolve_request(request.request_id)  # already resolved: not logged again

    with open(desk.log_path, encoding="utf-8") as log_file:
        records = [json.loads(line) for line in log_file]
    assert [r["status"] for r in records] == ["IN_PROGRESS", "RESOLVED"]
    assert records[-1]["assigned_tech_name"] == desk.technician_for(request).name

    with open(desk.report_csv_path, encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))
    assert len(rows) == 1 and rows[0]["status"] == "RESOLVED"


def test_numbering_continues_from_existing_log(desk, technicians):
    desk.open_request_for_alert(camera_alert())
    desk.open_request_for_alert(mic_alert())

    reopened = MaintenanceDesk(technicians, log_path=desk.log_path, report_csv_path=None)
    assert reopened.open_request_for_alert(camera_alert()).request_id == "MR-0003"
