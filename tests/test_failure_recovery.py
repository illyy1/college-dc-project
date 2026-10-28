"""Real-world failures the system must survive: the CSV report locked by
Excel, a diagnostic that crashes, a log line half-written when the program
was killed, the `--report` command end to end, a file held for a moment by
another program, a log that can't be written, a monitor that crashes, and
main.py explaining a failure instead of printing a traceback. Each failure is caused at
the external dependency (the file system, a component's own test, a device);
the code that has to cope with it always runs for real. main.py's tests make
the step it calls fail, as the other main.py tests in test_stats_viewer.py do."""

import builtins
import csv
import errno
import json
import os
import sys
from unittest import mock

import pytest

import main
from fake_dashboard import ScriptedMic
from hardware_monitor import processing
from hardware_monitor.context_managers import DiagnosticConnection
from hardware_monitor.dashboard import MONITOR_RESTART_SECONDS
from hardware_monitor.maintenance import MaintenanceDesk
from hardware_monitor.models import Camera, Microphone, Workstation
from hardware_monitor.processing import run_diagnostics_and_log_alerts
from hardware_monitor.reports import publish_maintenance_reports
from hardware_monitor.repository import (
    FILE_RETRY_BACKOFF_SECONDS,
    FILE_WRITE_ATTEMPTS,
    DataFileError,
    append_jsonl_record,
    delete_log,
    load_maintenance_history,
    load_technicians_from_jsonl,
    load_workstations_from_jsonl,
)


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


def test_a_locked_csv_does_not_stop_a_call_being_opened(desk, logged, alerts):
    with excel_has_the_csv_open():
        request = desk.open_request_for_alert(alerts.camera())

    assert request.status == "IN_PROGRESS"
    assert [r.request_id for r in load_maintenance_history(desk.log_path)] == [request.request_id]
    output = logged()
    assert "Could not update" in output and "is it open in Excel?" in output
    assert "The call was still saved" in output


def test_a_locked_csv_does_not_stop_a_call_being_resolved(desk, logged, alerts):
    request = desk.open_request_for_alert(alerts.camera())
    with excel_has_the_csv_open():
        desk.resolve_request(request.request_id)

    assert load_maintenance_history(desk.log_path)[0].status == "RESOLVED"
    assert "is it open in Excel?" in logged()


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

def test_a_crashing_diagnostic_is_marked_error_and_alerted(tmp_path, logged, alert_manager):
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
    assert "Session failed with error (OSError): USB device not responding" in logged()
    assert DiagnosticConnection.available_ports() == list(DiagnosticConnection.PORT_POOL)


def test_one_crash_does_not_stop_the_sweep(tmp_path, alert_manager):
    workstations = []
    for n in (1, 2, 3):
        workstation = Workstation(f"WS-{n}", "Desk")
        workstation.add_component(Camera(f"CAM-{n}", "Cam", f"WS-{n}", resolution="0x0"))  # all faulty
        workstations.append(workstation)
    manager = alert_manager
    crashing: Camera = workstations[0].components[0]

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


def test_the_desk_keeps_working_after_a_killed_run(technicians, make_call, write_jsonl, logged, alerts):
    log = write_jsonl("maintenance.jsonl", make_call(1, "CAMERA_FAILURE", "T1", "IN_PROGRESS"), fragment=CUT_OFF)

    desk = MaintenanceDesk(technicians, log_path=log, report_csv_path=None)
    assert "Skipping corrupt line 2" in logged()
    request = desk.open_request_for_alert(alerts.camera())

    assert request.request_id == "MR-0002"  # numbering ignores the unreadable fragment
    reopened = load_maintenance_history(log)
    assert [r.request_id for r in reopened] == ["MR-0001", "MR-0002"]


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


# --- 5. a file held for a moment by antivirus, OneDrive or the search indexer ---

REAL_OPEN = builtins.open


def file_locked(module, path, times=None, error=None):
    """Opening `path` from hardware_monitor.<module> fails `times` times (None:
    every time), by default with the PermissionError Windows gives while another
    program holds the file. Every other file opens normally."""
    error = error or PermissionError(13, "Permission denied")
    left = [times]

    def fake_open(file, *args, **kwargs):
        if os.path.abspath(file) == os.path.abspath(path) and (left[0] is None or left[0] > 0):
            if left[0] is not None:
                left[0] -= 1
            raise error
        return REAL_OPEN(file, *args, **kwargs)

    return mock.patch(f"hardware_monitor.{module}.open", create=True, side_effect=fake_open)


DISK_FULL = OSError(errno.ENOSPC, "No space left on device")
FILE_WAITS = [FILE_RETRY_BACKOFF_SECONDS * 2 ** n for n in range(FILE_WRITE_ATTEMPTS - 1)]


def test_a_log_held_for_a_moment_still_gets_the_record(make_call, write_jsonl, retry_waits):
    log = write_jsonl("log.jsonl", make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED"))
    with file_locked("repository", log, times=FILE_WRITE_ATTEMPTS - 1):
        append_jsonl_record(log, {"request_id": "MR-0002", "status": "IN_PROGRESS"})

    assert [r.request_id for r in load_maintenance_history(log)] == ["MR-0001", "MR-0002"]
    assert retry_waits == FILE_WAITS


@pytest.mark.parametrize("loader, line", [
    (load_workstations_from_jsonl, {"component_type": "Camera", "component_id": "CAM-1", "workstation_id": "WS-1"}),
    (load_technicians_from_jsonl, {"tech_id": "T1", "name": "Dana Levi"}),
    (load_maintenance_history, {"request_id": "MR-0001", "status": "IN_PROGRESS"}),
], ids=["fleet", "technicians", "maintenance-log"])
def test_a_file_held_for_a_moment_is_still_read(write_jsonl, retry_waits, loader, line):
    path = write_jsonl("held.jsonl", line)
    with file_locked("repository", path, times=1):
        assert len(loader(path)) == 1
    assert retry_waits == FILE_WAITS[:1]


def test_a_demo_log_held_for_a_moment_is_still_reset(write_jsonl, retry_waits):
    log = write_jsonl("demo_log.jsonl", {"alert_id": 1})
    real_remove, calls = os.remove, []

    def held_once(path):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError(13, "Permission denied")
        real_remove(path)

    with mock.patch("hardware_monitor.repository.os.remove", side_effect=held_once):
        delete_log(log)
    assert not os.path.exists(log) and len(retry_waits) == 1


def test_a_report_held_for_a_moment_is_still_written(reports_log, tmp_path):
    csv_path = str(tmp_path / "report.csv")
    with file_locked("reports", csv_path, times=1):
        publish_maintenance_reports(log_path=reports_log, csv_path=csv_path)
    assert [r["request_id"] for r in read_csv(csv_path)] == ["MR-0001", "MR-0002"]


# --- 6. the maintenance log cannot be written ---

def test_a_call_is_kept_when_the_log_cannot_be_written_and_saved_on_its_next_change(desk, alerts, logged):
    with file_locked("repository", desk.log_path):
        request = desk.open_request_for_alert(alerts.camera())

    assert request.status == "IN_PROGRESS" and desk.open_requests() == [request]
    assert f"Could not save call {request.request_id}" in logged()

    desk.resolve_request(request.request_id)  # the log works again
    (saved,) = load_maintenance_history(desk.log_path)
    assert (saved.request_id, saved.status) == (request.request_id, "RESOLVED")
    assert saved.assigned_tech_name == desk.technician_for(request).name


def test_a_csv_that_cannot_be_written_for_another_reason_does_not_stop_the_call(desk, alerts, logged, retry_waits):
    with file_locked("reports", desk.report_csv_path, error=DISK_FULL):
        request = desk.open_request_for_alert(alerts.camera())

    assert retry_waits == []  # a full disk is not a moment's lock: not retried
    assert [r.request_id for r in load_maintenance_history(desk.log_path)] == [request.request_id]
    output = logged()
    assert "Could not update" in output and "No space left" in output and "Excel" not in output


# --- 7. the live dashboard keeps monitoring ---

def test_a_fault_still_gets_its_call_when_the_alert_log_cannot_be_written(dash, logged):
    with file_locked("repository", processing.LOG_PATH):
        dash.app.show_alert("camera", "Camera Disconnected", "no frame")

    assert dash.app.open_requests["camera"].status == "IN_PROGRESS"
    assert "Could not save the camera alert" in logged()


class MicThatVanishesOnce(ScriptedMic):
    """A microphone whose first check fails, like a USB device unplugged mid-read."""

    def is_connected(self):
        self.calls += 1
        if self.calls == 1:
            raise OSError("device vanished mid-read")
        return True


def test_a_crashed_monitor_is_restarted(dash, logged):
    dash.app.mic_source = mic = MicThatVanishesOnce()
    dash.clock.stop_after = 3
    dash.app.start()

    assert mic.calls > 1  # still checked after the crash
    assert ("Microphone monitoring stopped by an error (OSError: device vanished mid-read); "
            f"restarting in {MONITOR_RESTART_SECONDS} s.") in logged()


# --- 8. main.py explains a failure instead of a traceback ---

@pytest.fixture
def run_main(monkeypatch):
    """run_main(*flags, demo_error=..., report_error=..., dashboard_error=...)
    runs main.main() with the demo, the report and the dashboard window mocked,
    each failing with the error given, and returns main's exit code."""
    def run(*flags, demo_error=None, report_error=None, dashboard_error=None):
        monkeypatch.setattr(sys, "argv", ["main.py", *flags])
        with mock.patch("main.run_demo", side_effect=demo_error), \
                mock.patch("main.publish_maintenance_reports", side_effect=report_error), \
                mock.patch("hardware_monitor.dashboard.HardwareMonitorApp") as app_class:
            app_class.return_value.start.side_effect = dashboard_error
            return main.main()
    return run


CSV_LOCKED = PermissionError(13, "Permission denied", "logs/maintenance_report.csv")


@pytest.mark.parametrize("flags", [("--report",), ("--no-stats",)], ids=["report", "after-the-dashboard"])
def test_a_report_that_cannot_be_written_is_explained(run_main, capsys, flags):
    assert run_main(*flags, report_error=CSV_LOCKED) == 1
    error = capsys.readouterr().err
    assert "Could not create the maintenance report" in error and "Is it open in Excel?" in error
    assert "logs/maintenance_report.csv" in error


def test_a_log_that_cannot_be_read_is_not_blamed_on_excel(run_main, capsys):
    log_locked = PermissionError(13, "Permission denied", "logs/maintenance_requests.jsonl")
    assert run_main("--report", report_error=log_locked) == 1
    error = capsys.readouterr().err
    assert "Could not create the maintenance report" in error and "logs/maintenance_requests.jsonl" in error
    assert "Excel" not in error


def test_a_dashboard_crash_is_not_hidden_by_a_failed_report(run_main):
    with pytest.raises(RuntimeError, match="dashboard crashed"):
        run_main("--no-stats", report_error=CSV_LOCKED, dashboard_error=RuntimeError("dashboard crashed"))


@pytest.mark.parametrize("problem", [
    FileNotFoundError(2, "No such file or directory", "data/sample_data.jsonl"),
    DataFileError("data/technicians.jsonl line 3: duplicate tech_id 'T1'."),
    PermissionError(13, "Permission denied", "logs/demo_hardware_history.jsonl"),  # e.g. a demo log still locked
], ids=["missing", "broken", "no-permission"])
def test_a_file_problem_is_explained(run_main, capsys, problem):
    assert run_main("--no-dashboard", demo_error=problem) == 1
    assert capsys.readouterr().err == f"Cannot start: {problem}\n"


def test_a_missing_tkinter_is_explained(monkeypatch, capsys):
    # Python installed without Tk (some Linux builds): --dashboard explains instead of a traceback
    monkeypatch.setitem(sys.modules, "tkinter", None)  # what a missing module looks like to import
    monkeypatch.delitem(sys.modules, "hardware_monitor.dashboard", raising=False)
    monkeypatch.setattr(sys, "argv", ["main.py", "--dashboard"])
    with mock.patch("main.run_demo"), mock.patch("main.publish_maintenance_reports"):
        assert main.main() == 1
    error = capsys.readouterr().err
    assert "needs tkinter" in error and "python main.py" in error


def test_a_bug_is_not_reported_as_a_data_problem(run_main):
    with pytest.raises(ValueError, match="a bug"):
        run_main("--no-dashboard", demo_error=ValueError("a bug"))


