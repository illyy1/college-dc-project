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

def killed_mid_write(path, complete_records, fragment='{"request_id": "MR-0099", "sta'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in complete_records) + fragment, encoding="utf-8")


def test_the_next_record_starts_on_a_fresh_line(tmp_path):
    log = tmp_path / "log.jsonl"
    killed_mid_write(log, [{"request_id": "MR-0001", "status": "RESOLVED"}])

    append_jsonl_record(str(log), {"request_id": "MR-0002", "status": "IN_PROGRESS"})

    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[1] == '{"request_id": "MR-0099", "sta'               # the fragment is left alone
    assert json.loads(lines[2]) == {"request_id": "MR-0002", "status": "IN_PROGRESS"}  # not glued onto it


def test_appending_to_a_clean_log_adds_no_blank_line(tmp_path):
    log = tmp_path / "log.jsonl"
    killed_mid_write(log, [{"request_id": "MR-0001"}], fragment="")
    append_jsonl_record(str(log), {"request_id": "MR-0002"})
    assert len(log.read_text(encoding="utf-8").splitlines()) == 2


def test_the_desk_keeps_working_after_a_killed_run(technicians, tmp_path, capsys, alerts):
    log = tmp_path / "maintenance.jsonl"
    killed_mid_write(log, [{"request_id": "MR-0001", "status": "IN_PROGRESS", "assigned_tech_id": "T1",
                            "opened_at": "2026-10-01T09:00:00"}])

    desk = MaintenanceDesk(technicians, log_path=str(log), report_csv_path=None)
    assert "skipping corrupt line 2" in capsys.readouterr().out
    request = desk.open_request_for_alert(alerts.camera())

    assert request.request_id == "MR-0002"  # numbering ignores the unreadable fragment
    reopened = load_maintenance_history(str(log))
    assert [r["request_id"] for r in reopened] == ["MR-0001", "MR-0002"]


# --- 4. the `--report` command, end to end ---

@pytest.fixture
def maintenance_log(tmp_path):
    log = tmp_path / "maintenance.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in [
        {"request_id": "MR-0001", "status": "IN_PROGRESS", "priority": 1, "error_type": "CAMERA_FAILURE",
         "workstation_id": "WS-104", "component_id": "CAM-104", "assigned_tech_id": "T1",
         "assigned_tech_name": "Dana Levi", "opened_at": "2026-10-01T09:00:00", "resolved_at": None},
        {"request_id": "MR-0001", "status": "RESOLVED", "priority": 1, "error_type": "CAMERA_FAILURE",
         "workstation_id": "WS-104", "component_id": "CAM-104", "assigned_tech_id": "T1",
         "assigned_tech_name": "Dana Levi", "opened_at": "2026-10-01T09:00:00",
         "resolved_at": "2026-10-01T10:00:00"},
    ]) + "\n", encoding="utf-8")
    return log


def test_report_prints_the_history_and_exports_the_csv(maintenance_log, tmp_path, capsys):
    csv_path = tmp_path / "exports" / "report.csv"  # the folder doesn't exist yet
    publish_maintenance_reports(log_path=str(maintenance_log), csv_path=str(csv_path))

    output = capsys.readouterr().out
    assert "MR-0001 | CAMERA_FAILURE" in output and "Dana Levi: 1" in output
    assert f"[REPORT] Maintenance history exported to {csv_path} (1 call(s))" in output
    (row,) = read_csv(csv_path)
    assert (row["request_id"], row["status"], row["assigned_tech_name"]) == ("MR-0001", "RESOLVED", "Dana Levi")


def test_report_with_no_history_yet(tmp_path, capsys):
    csv_path = tmp_path / "report.csv"
    publish_maintenance_reports(log_path=str(tmp_path / "none_yet.jsonl"), csv_path=str(csv_path))

    assert "(0 call(s))" in capsys.readouterr().out
    assert read_csv(csv_path) == []  # just the header


def test_report_fails_loudly_if_the_csv_cannot_be_written(maintenance_log, tmp_path):
    # unlike the automatic refresh, an explicit --report must not hide the problem
    with excel_has_the_csv_open(), pytest.raises(PermissionError):
        publish_maintenance_reports(log_path=str(maintenance_log), csv_path=str(tmp_path / "report.csv"))
