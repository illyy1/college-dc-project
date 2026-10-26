"""Business application: Gemini's triage of a fault (gemini_client.Triage)
drives what the system decides, saves and reports; it isn't just printed.

What the code must do (TDD specification; the API client itself is in
tests/test_gemini_client.py):
- MaintenanceDesk.apply_triage(request_id, triage): the call takes the
    more urgent of its own priority (the hardware rule) and Gemini's (lower
    is more urgent), keeps the likely cause and the estimated repair time,
    and is saved to the log. An unknown call raises ValueError.
- MaintenanceRequest gains `cause` and `estimated_minutes` (None until a
    triage); from_dict / to_dict and the CSV report carry them.
- The dashboard applies each triage to its call, and labels the GitHub
    issue with the call's final priority ("priority-N"). When Gemini fails,
    the call and the labels are left as they were.
- reports.triage_summary(history) -> TriageSummary(triaged_calls, causes,
    minutes_by_technician, open_backlog_minutes): the most common causes
    (most first, ties alphabetical), the estimated minutes per technician,
    and the estimated work still open. The history report prints it."""

import csv
from types import SimpleNamespace

import pytest

from fake_gemini import TRIAGE, api_error
from hardware_monitor.models import MaintenanceRequest
from hardware_monitor.reports import (
    TriageSummary,
    export_maintenance_history_csv,
    generate_maintenance_history_report,
    triage_summary,
)
from hardware_monitor.repository import load_maintenance_history

from conftest import as_requests


def triage(priority=2, cause="cable", minutes=15):
    """What apply_triage reads from a gemini_client.Triage."""
    return SimpleNamespace(priority=priority, cause=cause, repair_minutes=minutes, steps=("Reseat the USB cable.",))


# --- the desk: a triage updates the call ---

@pytest.mark.parametrize("rule, ai, final", [(4, 2, 2), (1, 4, 1), (3, 3, 3)],
                         ids=["gemini-more-urgent", "rule-more-urgent", "the-same"])
def test_the_call_takes_the_more_urgent_priority(desk, alerts, rule, ai, final):
    # Gemini can raise a call's urgency, never lower what the hardware rule set
    request = desk.open_request_for_alert(alerts.camera(priority=rule))
    desk.apply_triage(request.request_id, triage(priority=ai))
    assert request.priority == final


def test_the_call_keeps_the_cause_and_the_estimate(desk, alerts):
    request = desk.open_request_for_alert(alerts.camera())
    assert (request.cause, request.estimated_minutes) == (None, None)  # before any triage

    desk.apply_triage(request.request_id, triage(cause="driver", minutes=30))
    assert (request.cause, request.estimated_minutes) == ("driver", 30)


def test_the_triaged_call_is_saved(desk, alerts):
    request = desk.open_request_for_alert(alerts.camera(priority=4))
    desk.apply_triage(request.request_id, triage(priority=2, cause="driver", minutes=30))

    (saved,) = load_maintenance_history(desk.log_path)
    assert (saved.priority, saved.cause, saved.estimated_minutes) == (2, "driver", 30)


def test_a_restarted_desk_keeps_the_triage_of_an_open_call(desk, make_desk, alerts):
    request = desk.open_request_for_alert(alerts.camera(priority=4))
    desk.apply_triage(request.request_id, triage(priority=2, cause="driver", minutes=30))

    restored = make_desk().requests[request.request_id]  # same log: a restart
    assert (restored.priority, restored.cause, restored.estimated_minutes) == (2, "driver", 30)


def test_triaging_an_unknown_call_raises(desk):
    with pytest.raises(ValueError, match="Maintenance request 'MR-9999' does not exist"):
        desk.apply_triage("MR-9999", triage())


# --- the call's new fields, in the log and the CSV ---

def test_a_saved_triage_is_read_back(make_call):
    request = MaintenanceRequest.from_dict(
        make_call(1, "CAMERA_FAILURE", "T1", "IN_PROGRESS", cause="cable", estimated_minutes=15))
    assert (request.cause, request.estimated_minutes) == ("cable", 15)


def test_a_call_saved_before_triage_existed_has_none(make_call):
    request = MaintenanceRequest.from_dict(make_call(1, "CAMERA_FAILURE", "T1", "IN_PROGRESS"))
    assert (request.cause, request.estimated_minutes) == (None, None)


@pytest.mark.parametrize("change, field", [
    ({"cause": 3}, "cause"),
    ({"estimated_minutes": "15"}, "estimated_minutes"),
    ({"estimated_minutes": True}, "estimated_minutes"),
], ids=["cause-a-number", "minutes-as-text", "minutes-as-bool"])
def test_a_wrongly_typed_triage_field_raises(make_call, change, field):
    with pytest.raises(ValueError, match=field):
        MaintenanceRequest.from_dict({**make_call(1, "CAMERA_FAILURE", "T1", "IN_PROGRESS"), **change})


def test_the_triage_round_trips(desk, alerts):
    request = desk.open_request_for_alert(alerts.camera())
    desk.apply_triage(request.request_id, triage(cause="driver", minutes=30))
    assert MaintenanceRequest.from_dict(request.to_dict()).to_dict() == request.to_dict()


def test_the_csv_report_has_the_cause_and_the_estimate(desk, alerts, tmp_path):
    request = desk.open_request_for_alert(alerts.camera())
    desk.apply_triage(request.request_id, triage(cause="driver", minutes=30))

    csv_path = tmp_path / "report.csv"
    export_maintenance_history_csv(desk.log_path, str(csv_path), verbose=False)
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        (row,) = csv.DictReader(f)
    assert (row["cause"], row["estimated_minutes"]) == ("driver", "30")


# --- the dashboard: each triage is applied, and labels the issue ---

def send_with_call(ai_dash, alerts, priority):
    """A camera fault whose call the hardware rule gave `priority`, sent to GitHub."""
    request = ai_dash.app.maintenance_desk.open_request_for_alert(
        alerts.camera(component_id="CAM-LOCAL", priority=priority))
    ai_dash.app.send_github_alert("camera", "Camera Disconnected", "no frame", request)
    return request


def test_the_dashboard_applies_the_triage_to_the_call(ai_dash, alerts):
    request = send_with_call(ai_dash, alerts, priority=4)

    assert request.priority == TRIAGE["priority"]  # Gemini judged it more urgent
    saved = load_maintenance_history(ai_dash.app.maintenance_desk.log_path)[-1]
    assert (saved.cause, saved.estimated_minutes) == (TRIAGE["cause"], TRIAGE["repair_minutes"])


@pytest.mark.parametrize("rule, label", [(4, f"priority-{TRIAGE['priority']}"), (1, "priority-1")],
                         ids=["raised-by-gemini", "kept-from-the-rule"])
def test_the_issue_is_labelled_with_the_call_s_final_priority(ai_dash, alerts, rule, label):
    send_with_call(ai_dash, alerts, priority=rule)
    assert ai_dash.github.calls[-1].json["labels"] == ["hardware-alert", "camera", label]


def test_an_issue_without_a_call_is_labelled_with_gemini_s_priority(ai_dash):
    ai_dash.app.send_github_alert("camera", "Camera Disconnected", "no frame")
    assert ai_dash.github.calls[-1].json["labels"] == ["hardware-alert", "camera", f"priority-{TRIAGE['priority']}"]


def test_when_gemini_fails_the_call_and_the_labels_are_unchanged(ai_dash, alerts):
    ai_dash.gemini.reply = api_error(400, "API key not valid.")
    request = send_with_call(ai_dash, alerts, priority=4)

    assert (request.priority, request.cause, request.estimated_minutes) == (4, None, None)
    assert ai_dash.github.calls[-1].json["labels"] == ["hardware-alert", "camera"]


# --- the report: calculations from the triages ---

@pytest.fixture
def triaged_history(make_call):
    """Four calls, three triaged: cable twice (T1 15 min resolved, T2 20 min
    open), driver once (T1 30 min open); T3's call has no triage."""
    return as_requests([
        make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED", cause="cable", estimated_minutes=15),
        make_call(2, "CAMERA_FAILURE", "T2", "IN_PROGRESS", cause="cable", estimated_minutes=20),
        make_call(3, "MICROPHONE_SILENCE", "T1", "IN_PROGRESS", cause="driver", estimated_minutes=30),
        make_call(4, "CAMERA_FAILURE", "T3", "RESOLVED"),
    ])


def test_the_triage_summary(triaged_history):
    assert triage_summary(triaged_history) == TriageSummary(
        triaged_calls=3,
        causes=[("cable", 2), ("driver", 1)],          # most common first
        minutes_by_technician={"T1": 45, "T2": 20},     # estimated work per technician
        open_backlog_minutes=50,                        # still open: 20 + 30
    )


def test_causes_seen_equally_often_are_listed_alphabetically(make_call):
    history = as_requests([make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED", cause=cause, estimated_minutes=10)
                           for cause in ("software", "cable", "driver")])
    assert triage_summary(history).causes == [("cable", 1), ("driver", 1), ("software", 1)]


def test_no_triaged_calls_give_an_empty_summary(make_call):
    history = as_requests([make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED")])
    assert triage_summary(history) == TriageSummary(0, [], {}, 0)


def test_the_history_report_prints_the_summary(triaged_history, write_jsonl, capsys):
    log = write_jsonl("maintenance.jsonl", *(call.to_dict() for call in triaged_history))
    generate_maintenance_history_report(log)

    output = capsys.readouterr().out
    assert "Gemini" in output and "cable: 2" in output and "driver: 1" in output
    assert "50 min" in output  # the open backlog


def test_a_report_with_no_triaged_calls_has_no_ai_section(make_call, write_jsonl, capsys):
    generate_maintenance_history_report(write_jsonl("maintenance.jsonl", make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED")))
    assert "Gemini" not in capsys.readouterr().out
