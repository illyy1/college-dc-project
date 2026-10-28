"""TDD specification for hardware_monitor/stats.py (not written yet): the
numbers behind the live statistics viewer, computed from the maintenance log.

What the module must provide:
- compute_stats(history, technicians, recent_limit=10) -> Stats
    history: the records from load_maintenance_history (latest state per call)
    technicians: tech_id -> Technician, as loaded from technicians.jsonl
- load_stats(log_path, technicians_path, recent_limit=10) -> Stats
    reads both files; a missing log means no calls yet
- Stats: total_calls, open_calls, resolved_calls,
         alert_types (list of AlertTypeCount), technicians (list of
         TechnicianLoad), recent_calls (list of CallRow)
- AlertTypeCount: alert_type, total, open, resolved
- TechnicianLoad: tech_id, name, total, open, resolved, share (percent of all calls)
- CallRow: request_id, alert_type, error_type, workstation_id, technician, status, opened_at

Alert types are grouped by the error type's prefix: CAMERA_* -> "Camera",
MICROPHONE_* -> "Microphone", anything else -> "Other". "Open" means any
status other than RESOLVED.
"""


from datetime import datetime

import pytest

from hardware_monitor.stats import AlertTypeCount, CallRow, TechnicianLoad, compute_stats, load_stats
from hardware_monitor.repository import load_maintenance_history

from conftest import BAD_FIELDS, as_requests



# --- test data (make_call and history are in conftest.py) ---

@pytest.fixture
def write_log(write_jsonl):
    """write_log(records): a temporary maintenance log holding these records."""
    return lambda records: write_jsonl("maintenance_requests.jsonl", *records)


# --- totals ---

def test_totals(history, technicians):
    stats = compute_stats(as_requests(history), technicians)
    assert (stats.total_calls, stats.open_calls, stats.resolved_calls) == (7, 3, 4)


def test_no_calls_yet(technicians):
    stats = compute_stats([], technicians)
    assert (stats.total_calls, stats.open_calls, stats.resolved_calls) == (0, 0, 0)
    assert stats.alert_types == [] and stats.recent_calls == []


# --- alerts by type ---

def test_alerts_are_grouped_by_type_busiest_first(history, technicians):
    assert compute_stats(as_requests(history), technicians).alert_types == [
        AlertTypeCount("Camera", total=4, open=2, resolved=2),
        AlertTypeCount("Microphone", total=2, open=1, resolved=1),
        AlertTypeCount("Other", total=1, open=0, resolved=1),
    ]


@pytest.mark.parametrize("error_type, alert_type", [
    ("CAMERA_FAILURE", "Camera"),
    ("camera_frame_error", "Camera"),
    ("MICROPHONE_SILENCE", "Microphone"),
    ("MICROPHONE_DISCONNECTED", "Microphone"),
    ("MONITOR_OFF", "Other"),
    ("", "Other"),
], ids=["camera-failure", "lowercase", "mic-silence", "mic-disconnected", "unknown-device", "empty"])
def test_each_error_type_maps_to_an_alert_type(make_call, technicians, error_type, alert_type):
    stats = compute_stats(as_requests([make_call(1, error_type, "T1", "RESOLVED", "09:00")]), technicians)
    assert [t.alert_type for t in stats.alert_types] == [alert_type]


def test_types_with_equal_counts_are_listed_alphabetically(make_call, technicians):
    calls = [make_call(1, "MICROPHONE_SILENCE", "T2", "RESOLVED", "09:00"),
             make_call(2, "CAMERA_FAILURE", "T1", "RESOLVED", "09:10")]
    assert [t.alert_type for t in compute_stats(as_requests(calls), technicians).alert_types] == ["Camera", "Microphone"]


# --- the spread across technicians ---

def test_technician_spread_busiest_first(history, technicians):
    loads = compute_stats(as_requests(history), technicians).technicians

    assert [(t.tech_id, t.name, t.total, t.open, t.resolved) for t in loads] == [
        ("T1", "Dana Levi", 3, 1, 2),
        ("T2", "Yossi Cohen", 2, 1, 1),
        ("T4", "Avi Peretz", 1, 0, 1),   # equal counts: alphabetical by name
        ("T3", "Noa Mizrahi", 1, 1, 0),
    ]


def test_shares_are_percentages_of_all_calls(history, technicians):
    shares = {t.tech_id: t.share for t in compute_stats(as_requests(history), technicians).technicians}

    assert shares["T1"] == pytest.approx(300 / 7)
    assert shares["T2"] == pytest.approx(200 / 7)
    assert shares["T3"] == shares["T4"] == pytest.approx(100 / 7)
    assert sum(shares.values()) == pytest.approx(100)


def test_technicians_without_calls_are_still_listed(make_call, technicians):
    loads = compute_stats(as_requests([make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED", "09:00")]), technicians).technicians

    assert [t.tech_id for t in loads] == ["T1", "T4", "T3", "T2"]  # T1 first, then the idle ones by name
    idle = [t for t in loads if t.tech_id != "T1"]
    assert all((t.total, t.open, t.resolved, t.share) == (0, 0, 0, 0) for t in idle)


def test_no_calls_gives_every_technician_a_zero_share(technicians):
    loads = compute_stats([], technicians).technicians
    assert len(loads) == len(technicians) and all(t.share == 0 for t in loads)  # and no division by zero


def test_unassigned_calls_get_their_own_row(make_call, technicians):
    calls = [make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED", "09:00"),
             make_call(2, "CAMERA_FAILURE", None, "OPEN", "09:10"),
             make_call(3, "CAMERA_FAILURE", None, "OPEN", "09:20")]
    loads = compute_stats(as_requests(calls), technicians).technicians

    assert loads[0] == TechnicianLoad(None, "Unassigned", total=2, open=2, resolved=0, share=pytest.approx(200 / 3))


def test_no_unassigned_row_when_every_call_has_a_technician(history, technicians):
    assert "Unassigned" not in [t.name for t in compute_stats(as_requests(history), technicians).technicians]


def test_a_technician_missing_from_the_data_file_is_still_counted(make_call, technicians):
    # the log names T9, who has since been removed from technicians.jsonl
    gone = {**make_call(1, "CAMERA_FAILURE", "T9", "RESOLVED", "09:00"), "assigned_tech_name": "Former Tech"}
    loads = compute_stats(as_requests([gone]), technicians).technicians

    assert (loads[0].tech_id, loads[0].name, loads[0].total) == ("T9", "Former Tech", 1)


def test_a_missing_technician_without_a_logged_name_shows_the_id(make_call, technicians):
    gone = {**make_call(1, "CAMERA_FAILURE", "T9", "RESOLVED", "09:00"), "assigned_tech_name": None}
    assert compute_stats(as_requests([gone]), technicians).technicians[0].name == "T9"


def test_counts_use_the_current_technician_name(make_call, technicians):
    # the name stored in the log is a snapshot; the data file is the current truth
    renamed = {**make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED", "09:00"), "assigned_tech_name": "Old Name"}
    assert compute_stats(as_requests([renamed]), technicians).technicians[0].name == "Dana Levi"


# --- who took each alert ---

def test_recent_calls_newest_first_with_who_took_them(history, technicians):
    recent = compute_stats(as_requests(history), technicians).recent_calls

    assert recent[0] == CallRow("MR-0007", "Camera", "CAMERA_FAILURE", "WS-104", "Dana Levi", "IN_PROGRESS",
                                datetime(2026, 10, 20, 10, 0))  # the open time as a datetime, not text
    assert [c.request_id for c in recent] == [f"MR-{n:04d}" for n in range(7, 0, -1)]
    assert [c.technician for c in recent[:3]] == ["Dana Levi", "Avi Peretz", "Dana Levi"]


@pytest.mark.parametrize("limit, shown", [(3, 3), (7, 7), (50, 7)], ids=["fewer", "exactly-all", "more-than-there-are"])
def test_recent_calls_are_limited(history, technicians, limit, shown):
    recent = compute_stats(as_requests(history), technicians, recent_limit=limit).recent_calls
    assert len(recent) == shown and recent[0].request_id == "MR-0007"


def test_an_unassigned_recent_call_says_so(make_call, technicians):
    recent = compute_stats(as_requests([make_call(1, "CAMERA_FAILURE", None, "OPEN", "09:00")]), technicians).recent_calls
    assert recent[0].technician == "Unassigned"


def test_calls_opened_at_the_same_moment_newest_number_first(make_call, technicians):
    calls = [make_call(n, "CAMERA_FAILURE", "T1", "OPEN", "09:00") for n in (1, 2, 3)]
    assert [c.request_id for c in compute_stats(as_requests(calls), technicians).recent_calls] == ["MR-0003", "MR-0002", "MR-0001"]


def test_a_call_without_an_open_time_goes_last(make_call, technicians):
    undated = {**make_call(1, "CAMERA_FAILURE", "T1", "OPEN", "09:00"), "opened_at": None}
    dated = make_call(2, "CAMERA_FAILURE", "T3", "OPEN", "08:00")
    recent = compute_stats(as_requests([undated, dated]), technicians).recent_calls
    assert [c.request_id for c in recent] == ["MR-0002", "MR-0001"]


# --- reading the files ---

def test_load_stats_reads_the_log_and_the_technicians(history, write_log, technicians_file):
    stats = load_stats(write_log(history), technicians_file)
    assert stats.total_calls == 7 and stats.technicians[0].name == "Dana Levi"


def test_load_stats_uses_the_latest_state_of_each_call(make_call, write_log, technicians_file):
    opened = make_call(1, "CAMERA_FAILURE", "T1", "IN_PROGRESS", "09:00")
    resolved = make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED", "09:00")
    stats = load_stats(write_log([opened, resolved]), technicians_file)

    assert (stats.total_calls, stats.open_calls, stats.resolved_calls) == (1, 0, 1)


def test_load_stats_with_no_log_yet(tmp_path, technicians_file, technicians):
    stats = load_stats(str(tmp_path / "not_created_yet.jsonl"), technicians_file)
    assert stats.total_calls == 0 and len(stats.technicians) == len(technicians)


# --- a saved call with a field of the wrong type ---

@pytest.mark.parametrize("bad_field", BAD_FIELDS)
def test_a_call_with_a_wrongly_typed_field_is_skipped(bad_field, history, make_bad_call, write_log,
                                                      technicians_file, logged):
    with_bad_call = load_stats(write_log([*history, make_bad_call(8, "T1", bad_field)]), technicians_file)  # line 8

    assert with_bad_call == load_stats(write_log(history), technicians_file)  # as if it weren't there
    output = logged()
    assert "Skipping" in output and "line 8" in output


def test_a_wrongly_typed_call_is_reported_once_when_read_again(history, make_bad_call, write_log,
                                                              technicians_file, logged):
    path = write_log([*history, make_bad_call(8, "T1")])
    reported = set()
    for _ in range(3):
        load_stats(path, technicians_file, reported=reported)
    assert logged().count("line 8") == 1


def test_load_stats_skips_a_half_written_line(history, write_jsonl, capsys, technicians_file):
    log = write_jsonl("maintenance_requests.jsonl", *history, fragment='{"request_id": "MR-0008", "sta')
    assert load_stats(log, technicians_file).total_calls == 7


def test_load_stats_matches_what_the_desk_wrote(desk, alerts, technicians_file):
    # end to end: calls opened and resolved by the real MaintenanceDesk
    first = desk.open_request_for_alert(alerts.camera())
    desk.open_request_for_alert(alerts.microphone())
    desk.open_request_for_alert(alerts.camera())
    desk.resolve_request(first.request_id)

    stats = load_stats(desk.log_path, technicians_file)

    assert (stats.total_calls, stats.open_calls, stats.resolved_calls) == (3, 2, 1)
    assert {t.alert_type: t.total for t in stats.alert_types} == {"Camera": 2, "Microphone": 1}
    assert sum(t.total for t in stats.technicians) == 3
    assert [c.request_id for c in stats.recent_calls] == ["MR-0003", "MR-0002", "MR-0001"]
    assert len(load_maintenance_history(desk.log_path)) == 3
