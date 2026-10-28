"""Data transformation: raw data turned into the system's own objects.

The data files already become objects through the from_dict factories
(Workstation, HardwareComponent, Technician: test_design_patterns.py), and
the API replies become IssueResult / SuggestionResult. This file is the TDD
specification for the one raw source that is still passed around as dicts,
the maintenance log:

- MaintenanceRequest.from_dict(record) -> MaintenanceRequest
    request_id (required text); alert_id (int or None); priority (int,
    default 5); error_type, workstation_id, component_id (text, default "");
    status (one of VALID_STATUSES, default OPEN); assigned_tech_id and
    assigned_tech_name (text or None); opened_at and resolved_at (ISO text
    -> datetime, or None). A field of the wrong type raises ValueError
    naming it, like the other factories.
- load_maintenance_history() returns MaintenanceRequest objects (the latest
  state of each call), still skipping a damaged line with a warning.
- to_dict() and from_dict() round-trip: what the desk saves loads back the same."""

from datetime import datetime

import pytest

from hardware_monitor.models import MaintenanceRequest
from hardware_monitor.repository import load_maintenance_history

from conftest import BAD_FIELDS


# --- one log record -> one MaintenanceRequest ---

def test_a_saved_call_becomes_a_maintenance_request(make_call):
    request = MaintenanceRequest.from_dict(make_call(7, "CAMERA_FAILURE", "T1", "RESOLVED", "10:00"))

    assert isinstance(request, MaintenanceRequest)
    assert (request.request_id, request.alert_id, request.priority) == ("MR-0007", 7, 1)
    assert (request.error_type, request.workstation_id, request.component_id) == ("CAMERA_FAILURE", "WS-104", "CAM-104")
    assert (request.status, request.assigned_tech_id, request.assigned_tech_name) == ("RESOLVED", "T1", "Dana Levi")


def test_the_times_become_datetimes(make_call):
    request = MaintenanceRequest.from_dict(make_call(7, "CAMERA_FAILURE", "T1", "RESOLVED", "10:00"))
    assert request.opened_at == datetime(2026, 10, 20, 10, 0)
    assert request.resolved_at == datetime(2026, 10, 20, 10, 0, 30)


def test_an_open_call_has_no_resolved_time(make_call):
    request = MaintenanceRequest.from_dict(make_call(7, "CAMERA_FAILURE", "T1", "IN_PROGRESS"))
    assert request.status == "IN_PROGRESS" and request.resolved_at is None


def test_fields_left_out_get_their_defaults():
    # an old or hand-written record may hold only its id
    request = MaintenanceRequest.from_dict({"request_id": "MR-0001"})

    assert (request.alert_id, request.priority, request.status) == (None, 5, "OPEN")
    assert (request.error_type, request.workstation_id, request.component_id) == ("", "", "")
    assert (request.assigned_tech_id, request.assigned_tech_name) == (None, None)
    assert request.opened_at is None and request.resolved_at is None  # unknown, not "now"


def test_unassigned_calls_keep_no_technician(make_call):
    request = MaintenanceRequest.from_dict(make_call(3, "CAMERA_FAILURE", None, "OPEN"))
    assert request.assigned_tech_id is None and request.assigned_tech_name is None


def test_what_the_desk_saves_loads_back_the_same(desk, alerts):
    request = desk.open_request_for_alert(alerts.camera())
    desk.resolve_request(request.request_id)

    loaded = MaintenanceRequest.from_dict(request.to_dict())
    assert loaded.to_dict() == request.to_dict()


# --- records that can't become a MaintenanceRequest ---

@pytest.mark.parametrize("bad_field", BAD_FIELDS)
def test_a_wrongly_typed_field_raises_naming_it(make_call, bad_field):
    (field,) = bad_field
    with pytest.raises(ValueError, match=field):
        MaintenanceRequest.from_dict({**make_call(1, "CAMERA_FAILURE", "T1", "IN_PROGRESS"), **bad_field})


@pytest.mark.parametrize("change, field", [
    ({"request_id": None}, "request_id"),
    ({"request_id": "   "}, "request_id"),
    ({"priority": "high"}, "priority"),
    ({"priority": True}, "priority"),
    ({"alert_id": "7"}, "alert_id"),
    ({"status": "LOST"}, "status"),
    ({"status": 3}, "status"),
    ({"resolved_at": "later"}, "resolved_at"),
    ({"assigned_tech_name": 12}, "assigned_tech_name"),
], ids=["no-id", "blank-id", "priority-as-text", "priority-as-bool", "alert-id-as-text", "unknown-status",
        "status-a-number", "resolved-time-not-a-date", "technician-name-a-number"])
def test_other_impossible_values_raise_naming_the_field(make_call, change, field):
    with pytest.raises(ValueError, match=field):
        MaintenanceRequest.from_dict({**make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED"), **change})


@pytest.mark.parametrize("record", [["MR-0001"], "MR-0001", None], ids=["a-list", "text", "null"])
def test_a_record_that_is_not_an_object_raises(record):
    with pytest.raises(ValueError, match="MaintenanceRequest record must be a JSON object"):
        MaintenanceRequest.from_dict(record)


# --- the whole log -> MaintenanceRequest objects ---

def test_the_log_loads_as_maintenance_requests(history, write_jsonl):
    loaded = load_maintenance_history(write_jsonl("maintenance.jsonl", *history))

    assert all(isinstance(call, MaintenanceRequest) for call in loaded)
    assert [call.request_id for call in loaded] == [f"MR-{n:04d}" for n in range(1, 8)]


def test_each_call_loads_in_its_latest_state(make_call, write_jsonl):
    log = write_jsonl("maintenance.jsonl",
                      make_call(1, "CAMERA_FAILURE", "T1", "IN_PROGRESS"),
                      make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED"))
    (call,) = load_maintenance_history(log)
    assert call.status == "RESOLVED" and call.resolved_at == datetime(2026, 10, 20, 9, 0, 30)


@pytest.mark.parametrize("bad_field", BAD_FIELDS)
def test_a_line_that_cant_become_a_call_is_skipped_with_a_warning(make_call, make_bad_call, write_jsonl,
                                                                  logged, bad_field):
    log = write_jsonl("maintenance.jsonl", make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED"),
                      make_bad_call(2, "T2", bad_field))

    assert [call.request_id for call in load_maintenance_history(log)] == ["MR-0001"]
    assert "Skipping" in logged()
