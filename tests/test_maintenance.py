"""The maintenance desk. Who gets a call: there are no specialists, so every
technician can take any open call, of any kind, on any workstation. The
technicians with the fewest calls saved in the maintenance log (every call
they were ever given, resolved ones and earlier runs included) have
priority, and the call goes to one of them picked at random.

The default strategy is FewestCallsStrategy(rng=random): rng.choice() is
given the tied technicians. Tests that need to know who is picked pass a
`picker` that records what it was offered and takes the first."""

import csv
import json
import random
from collections import Counter
from unittest import mock

import pytest

from hardware_monitor.maintenance import FewestCallsStrategy
from hardware_monitor.models import Technician
from hardware_monitor.reports import find_unknown_workstation_references


@pytest.fixture
def desk(make_desk):
    """This file's desk also writes the CSV report."""
    return make_desk(csv_report=True)


@pytest.fixture
def team():
    """Three technicians, A, B and C, none covering any particular workstation."""
    return {tid: Technician(tid, f"Tech {tid}") for tid in ("A", "B", "C")}


class Picker:
    """A predictable stand-in for random: records the technicians each draw
    was between, and takes the first of them."""

    def __init__(self):
        self.offered = []

    def choice(self, options):
        self.offered.append([t.tech_id for t in options])
        return options[0]


@pytest.fixture
def picker():
    return Picker()


@pytest.fixture
def picked_desk(make_desk, team, picker):
    """A desk for `team` whose random picks are recorded by `picker`."""
    return make_desk(team, assignment_strategy=FewestCallsStrategy(rng=picker))


def take_calls(desk, alerts, count, resolve=True, make=None):
    """Opens `count` calls one at a time (resolving each, like faults that clear
    quickly) and returns who got each one."""
    taken = []
    for _ in range(count):
        request = desk.open_request_for_alert(make() if make else alerts.camera())
        if resolve:
            desk.resolve_request(request.request_id)
        taken.append(request.assigned_tech_id)
    return taken


@pytest.fixture
def saved_log(tmp_path):
    """Writes maintenance-log records for calls a previous run saved."""
    def write(*tech_ids, status="RESOLVED"):
        path = tmp_path / "logs" / "maintenance_requests.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for number, tech_id in enumerate(tech_ids, start=1):
                f.write(json.dumps({"request_id": f"MR-{number:04d}", "status": status, "assigned_tech_id": tech_id,
                                    "error_type": "CAMERA_FAILURE", "workstation_id": "WS-104",
                                    "opened_at": f"2026-10-20T09:{number:02d}:00"}) + "\n")
        return str(path)
    return write


# --- the technicians ---

def test_technicians_load_from_data(technicians):
    assert [t.name for t in technicians.values()] == ["Dana Levi", "Yossi Cohen", "Noa Mizrahi", "Avi Peretz"]


def test_technicians_have_no_specialty(technicians):
    assert all(not hasattr(t, "specialty") for t in technicians.values())


def test_technician_coverage_matches_sample_data(technicians, workstations):
    # the workstation lists stay in the data (checked against sample_data.jsonl)
    # even though they no longer limit who takes a call
    assert technicians["T1"].workstation_ids == ("WS-101", "WS-102", "WS-103", "WS-104")
    assert find_unknown_workstation_references(technicians, workstations) == []


def test_dangling_workstation_reference_is_reported(technicians, workstations):
    technicians["T9"] = Technician("T9", "Ghost", workstation_ids=["WS-101", "WS-999"])
    assert find_unknown_workstation_references(technicians, workstations) == [("T9", "WS-999")]


def test_technician_rejects_malformed_workstation_ids():
    with pytest.raises(ValueError, match="Technician 'T9': workstation_ids must be a list"):
        Technician.from_dict({"tech_id": "T9", "name": "Ghost", "workstation_ids": "WS-101"})


# --- who gets a call ---

def test_everyone_takes_every_kind_of_call(desk, alerts):
    # four technicians, no history: the first four calls go to four different people,
    # whatever the device
    faults = [alerts.camera, alerts.microphone, alerts.camera, alerts.microphone]
    taken = [desk.open_request_for_alert(fault()).assigned_tech_id for fault in faults]
    assert sorted(taken) == ["T1", "T2", "T3", "T4"]


def test_the_fewest_calls_saved_gets_the_next_one(make_desk, team, alerts):
    desk = make_desk(team)
    assert sorted(take_calls(desk, alerts, 6)) == ["A", "A", "B", "B", "C", "C"]


def test_only_the_technicians_with_the_fewest_calls_are_drawn_from(picked_desk, picker, alerts):
    take_calls(picked_desk, alerts, 4)
    assert picker.offered == [
        ["A", "B", "C"],   # nobody has a call yet: all three
        ["B", "C"],        # A has 1
        ["C"],             # A and B have 1: only C is left at 0
        ["A", "B", "C"],   # all have 1 again
    ]


def test_resolved_calls_still_count(picked_desk, picker, alerts):
    # fewest calls saved, not fewest open: A and B are free right now, but
    # once C has had a call too, all three have 1 and all three are drawn from
    take_calls(picked_desk, alerts, 2)                  # A, B: resolved
    take_calls(picked_desk, alerts, 1, resolve=False)   # C: still open
    take_calls(picked_desk, alerts, 1)
    assert picker.offered[-1] == ["A", "B", "C"]


def test_a_busy_technician_with_fewer_calls_has_priority(picked_desk, picker, alerts):
    assert take_calls(picked_desk, alerts, 1) == ["A"]                 # A: 1, resolved
    assert take_calls(picked_desk, alerts, 1, resolve=False) == ["B"]  # B: 1, still open
    assert take_calls(picked_desk, alerts, 2) == ["C", "A"]            # C: 1; then A: 2
    # B is busy, but B and C have had fewer calls than A
    assert picker.offered[-1] == ["B", "C"]
    assert take_calls(picked_desk, alerts, 1) == ["B"]


def test_the_pick_among_equals_is_random(team):
    strategy = FewestCallsStrategy(rng=random.Random(2026))
    candidates = list(team.values())
    picks = Counter(strategy.choose(candidates, {}, {"A": 3, "B": 3, "C": 3}).tech_id for _ in range(3000))

    assert set(picks) == {"A", "B", "C"}
    assert all(800 < count < 1200 for count in picks.values())  # about 1000 each


def test_someone_with_more_calls_is_never_picked(team):
    strategy = FewestCallsStrategy(rng=random.Random(2026))
    candidates = list(team.values())
    picks = {strategy.choose(candidates, {}, {"A": 2, "B": 2, "C": 5}).tech_id for _ in range(500)}
    assert picks == {"A", "B"}


def test_a_technician_with_no_saved_calls_counts_as_zero(team):
    strategy = FewestCallsStrategy(rng=random.Random(2026))
    picks = {strategy.choose(list(team.values()), {}, {"A": 1, "B": 1}).tech_id for _ in range(50)}
    assert picks == {"C"}


def test_the_default_desk_picks_with_pythons_random(desk, alerts):
    with mock.patch("hardware_monitor.maintenance.random.choice", side_effect=lambda options: options[-1]) as choice:
        request = desk.open_request_for_alert(alerts.camera())

    (offered,), _ = choice.call_args
    assert [t.tech_id for t in offered] == ["T1", "T2", "T3", "T4"]
    assert request.assigned_tech_id == "T4"


def test_coverage_no_longer_limits_who_takes_a_call(make_desk, alerts):
    # X covers WS-104, Y covers nothing; both get WS-104 calls
    team = {"X": Technician("X", "Covers it", workstation_ids=["WS-104"]), "Y": Technician("Y", "Doesn't")}
    desk = make_desk(team)
    assert sorted(take_calls(desk, alerts, 2)) == ["X", "Y"]


def test_the_dashboards_own_machine_spreads_calls_to_everyone(desk, alerts):
    # the live dashboard's machine isn't in sample_data.jsonl; its camera and mic
    # faults come one at a time and clear quickly
    faults = [lambda: alerts.camera(workstation_id="Aliza-HomePC"),
              lambda: alerts.microphone(workstation_id="Aliza-HomePC")]
    taken = []
    for n in range(8):
        taken += take_calls(desk, alerts, 1, make=faults[n % 2])
    assert {tech: taken.count(tech) for tech in set(taken)} == {"T1": 2, "T2": 2, "T3": 2, "T4": 2}


def test_calls_saved_by_an_earlier_run_count(make_desk, team, alerts, saved_log):
    saved_log("A", "A", "B")                          # an earlier run: A had 2 calls, B had 1
    desk = make_desk(team)
    taken = take_calls(desk, alerts, 3)
    assert taken[0] == "C"                            # the only one with none
    assert sorted(taken[1:]) == ["B", "C"]            # then B and C (1 each), in random order
    assert Counter(taken) + Counter({"A": 2, "B": 1}) == {"A": 2, "B": 2, "C": 2}


def test_open_calls_saved_by_an_earlier_run_count_too(make_desk, team, alerts, saved_log):
    saved_log("A", "B", status="IN_PROGRESS")
    desk = make_desk(team)
    assert take_calls(desk, alerts, 1) == ["C"]


def test_a_technician_who_left_doesnt_affect_the_rest(make_desk, team, alerts, saved_log):
    saved_log("T42", "T42", "T42")                    # in the log, no longer in technicians.jsonl
    desk = make_desk(team)
    assert sorted(take_calls(desk, alerts, 3)) == ["A", "B", "C"]


def test_a_single_technician_takes_everything(make_desk, alerts):
    desk = make_desk({"T9": Technician("T9", "Solo")})
    assert take_calls(desk, alerts, 3, make=alerts.microphone) == ["T9", "T9", "T9"]


def test_no_technicians_raises(make_desk, alerts):
    desk = make_desk({})
    with pytest.raises(ValueError, match="No technicians are registered"):
        desk.open_request_for_alert(alerts.camera())
    assert desk.requests == {}  # nothing half-opened


# --- the call itself ---

def test_a_new_call_is_in_progress_and_numbered(desk, alerts):
    first = desk.open_request_for_alert(alerts.camera())
    second = desk.open_request_for_alert(alerts.microphone())
    assert first.status == second.status == "IN_PROGRESS"
    assert (first.request_id, second.request_id) == ("MR-0001", "MR-0002")


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
