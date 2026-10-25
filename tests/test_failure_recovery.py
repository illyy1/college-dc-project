"""Real-world failures the system must survive: the CSV report locked by
Excel, a diagnostic that crashes, a log line half-written when the program
was killed, and the `--report` command end to end. Each failure is caused at
the external dependency (the file system, a component's own test); the code
that has to cope with it always runs for real."""

import csv
import json
from unittest import mock

import pytest

from hardware_monitor.context_managers import DiagnosticConnection
from hardware_monitor.maintenance import MaintenanceDesk
from hardware_monitor.models import Camera, Microphone, Workstation
from hardware_monitor.processing import run_diagnostics_and_log_alerts
from hardware_monitor.reports import publish_maintenance_reports
from hardware_monitor.repository import append_jsonl_record, load_maintenance_history


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


# --- 1. the CSV report is locked (open in Excel) ---

def excel_has_the_csv_open():
    """Opening the CSV for writing fails the way it does on Windows while Excel holds it."""
    return mock.patch("hardware_monitor.reports.open", create=True,
                      side_effect=PermissionError(13, "Permission denied"))


@pytest.fixture
def desk(make_desk):
    """This file's desk also writes the CSV report."""
    return make_desk(csv_report=True)


def test_a_locked_csv_does_not_stop_a_call_being_opened(desk, capsys, alerts):
    with excel_has_the_csv_open():
        request = desk.open_request_for_alert(alerts.camera())

    assert request.status == "IN_PROGRESS"
    assert [r["request_id"] for r in load_maintenance_history(desk.log_path)] == [request.request_id]
    output = capsys.readouterr().out
    assert "could not update" in output and "is it open in Excel?" in output
    assert "The call was still saved" in output


def test_a_locked_csv_does_not_stop_a_call_being_resolved(desk, capsys, alerts):
    request = desk.open_request_for_alert(alerts.camera())
    with excel_has_the_csv_open():
        desk.resolve_request(request.request_id)

    assert load_maintenance_history(desk.log_path)[0]["status"] == "RESOLVED"
    assert "is it open in Excel?" in capsys.readouterr().out


def test_the_csv_catches_up_once_it_is_closed(desk, alerts):
    first = desk.open_request_for_alert(alerts.camera())
    with excel_has_the_csv_open():
        second = desk.open_request_for_alert(alerts.camera())
        desk.resolve_request(first.request_id)
    assert [r["request_id"] for r in read_csv(desk.report_csv_path)] == [first.request_id]  # stale

    third = desk.open_request_for_alert(alerts.camera())  # Excel closed: the next change refreshes it
    rows = read_csv(desk.report_csv_path)
    assert [r["request_id"] for r in rows] == [first.request_id, second.request_id, third.request_id]
    assert rows[0]["status"] == "RESOLVED"


# --- 2. a component's diagnostic crashes ---

def test_a_crashing_diagnostic_is_marked_error_and_alerted(tmp_path, capsys, alert_manager):
    workstation = Workstation("WS-1", "Desk")
    camera = Camera("CAM-1", "Cam", "WS-1")
    microphone = Microphone("MIC-1", "Mic", "WS-1")
    workstation.add_component(camera)
    workstation.add_component(microphone)
    manager = alert_manager

    with mock.patch.object(camera, "run_diagnostics", side_effect=OSError("USB device not responding")):
        run_diagnostics_and_log_alerts([workstation], manager)

    (alert,) = manager.alert_history  # the healthy microphone raised nothing
    assert (alert.component_id, alert.error_type, alert.priority) == ("CAM-1", "CAMERA_FAILURE", 1)
    assert camera.connection_status == "ERROR"
    assert microphone.connection_status == "ACTIVE"
    assert "Session failed with error (OSError): USB device not responding" in capsys.readouterr().out
    assert DiagnosticConnection.available_ports() == list(DiagnosticConnection.PORT_POOL)


def test_one_crash_does_not_stop_the_sweep(tmp_path, alert_manager):
    workstations = []
    for n in (1, 2, 3):
        workstation = Workstation(f"WS-{n}", "Desk")
        workstation.add_component(Camera(f"CAM-{n}", "Cam", f"WS-{n}", resolution="0x0"))  # all faulty
        workstations.append(workstation)
    manager = alert_manager
    crashing = workstations[0].components[0]

    with mock.patch.object(crashing, "run_diagnostics", side_effect=RuntimeError("driver crashed")):
        run_diagnostics_and_log_alerts(workstations, manager)

    assert [a.component_id for a in manager.alert_history] == ["CAM-1", "CAM-2", "CAM-3"]


# --- 3. a log line half-written when the program was killed ---

CUT_OFF = '{"request_id": "MR-0099", "sta'  # the line a killed run was writing


def test_the_next_record_starts_on_a_fresh_line(make_call, write_jsonl):
    log = write_jsonl("log.jsonl", make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED"), fragment=CUT_OFF)

    append_jsonl_record(log, {"request_id": "MR-0002", "status": "IN_PROGRESS"})

    with open(log, encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert lines[1] == '{"request_id": "MR-0099", "sta'               # the fragment is left alone
    assert json.loads(lines[2]) == {"request_id": "MR-0002", "status": "IN_PROGRESS"}  # not glued onto it


def test_appending_to_a_clean_log_adds_no_blank_line(make_call, write_jsonl):
    log = write_jsonl("log.jsonl", make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED"))
    append_jsonl_record(log, {"request_id": "MR-0002"})
    with open(log, encoding="utf-8") as f:
        assert len(f.read().splitlines()) == 2


def test_the_desk_keeps_working_after_a_killed_run(technicians, make_call, write_jsonl, capsys, alerts):
    log = write_jsonl("maintenance.jsonl", make_call(1, "CAMERA_FAILURE", "T1", "IN_PROGRESS"), fragment=CUT_OFF)

    desk = MaintenanceDesk(technicians, log_path=log, report_csv_path=None)
    assert "skipping corrupt line 2" in capsys.readouterr().out
    request = desk.open_request_for_alert(alerts.camera())

    assert request.request_id == "MR-0002"  # numbering ignores the unreadable fragment
    reopened = load_maintenance_history(log)
    assert [r["request_id"] for r in reopened] == ["MR-0001", "MR-0002"]


# --- 4. the `--report` command, end to end ---

def test_report_prints_the_history_and_exports_the_csv(reports_log, tmp_path, capsys):
    csv_path = tmp_path / "exports" / "report.csv"  # the folder doesn't exist yet
    publish_maintenance_reports(log_path=reports_log, csv_path=str(csv_path))

    output = capsys.readouterr().out
    assert "MR-0001 | CAMERA_FAILURE" in output and "Dana Levi: 1" in output
    assert f"[REPORT] Maintenance history exported to {csv_path} (2 call(s))" in output
    rows = read_csv(csv_path)
    assert [(r["request_id"], r["status"], r["assigned_tech_name"]) for r in rows] == \
        [("MR-0001", "RESOLVED", "Dana Levi"), ("MR-0002", "IN_PROGRESS", "")]


def test_report_with_no_history_yet(tmp_path, capsys):
    csv_path = tmp_path / "report.csv"
    publish_maintenance_reports(log_path=str(tmp_path / "none_yet.jsonl"), csv_path=str(csv_path))

    assert "(0 call(s))" in capsys.readouterr().out
    assert read_csv(csv_path) == []  # just the header


def test_report_fails_loudly_if_the_csv_cannot_be_written(reports_log, tmp_path):
    # unlike the automatic refresh, an explicit --report must not hide the problem
    with excel_has_the_csv_open(), pytest.raises(PermissionError):
        publish_maintenance_reports(log_path=reports_log, csv_path=str(tmp_path / "report.csv"))
