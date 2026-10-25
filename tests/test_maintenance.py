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

from conftest import TECHNICIANS_PATH
from fake_random import FirstPick


@pytest.fixture
def desk(make_desk):
    """This file's desk also writes the CSV report."""
    return make_desk(csv_report=True)


@pytest.fixture
def team():
    """Three technicians, A, B and C."""
    return {tid: Technician(tid, f"Tech {tid}") for tid in ("A", "B", "C")}


@pytest.fixture
def picker():
    """Stands in for random: records each draw and takes the first option."""
    return FirstPick()


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
def saved_log(make_call, write_jsonl):
    """saved_log(*tech_ids, status=...): calls a previous run saved in the
    desk's log (the log make_desk uses), one per technician listed."""
    def write(*tech_ids, status="RESOLVED"):
        calls = [make_call(n, "CAMERA_FAILURE", tech_id, status, f"09:{n:02d}") for n, tech_id in enumerate(tech_ids, 1)]
        return write_jsonl("logs/maintenance_requests.jsonl", *calls)
    return write


# --- the technicians ---

# The real data/technicians.jsonl changes as staff are hired and leave, so
# these tests check only what must hold for any staff.

def test_the_technician_file_loads(real_technicians):
    assert len(real_technicians) >= 1  # the desk needs someone to assign calls to
    for tech_id, tech in real_technicians.items():
        assert tech.tech_id == tech_id and tech.name.strip()


def test_technicians_have_no_specialty(real_technicians):
    assert all(not hasattr(t, "specialty") for t in real_technicians.values())


def test_technicians_have_no_workstation_lists(real_technicians):
    # anyone takes any call on any workstation, so technicians aren't tied to workstations
    for tech in real_technicians.values():
        assert not hasattr(tech, "workstation_ids") and not hasattr(tech, "covers")


def test_the_technician_file_has_only_ids_and_names():
    with open(TECHNICIANS_PATH, encoding="utf-8") as data_file:
        records = [json.loads(line) for line in data_file if line.strip()]
    assert all(set(record) == {"tech_id", "name"} for record in records)


@pytest.mark.parametrize("old_list", [["WS-101", "WS-102"], "WS-101", [1, 2]],
                         ids=["a-list", "not-a-list", "not-ids"])
def test_an_old_files_workstation_list_is_ignored(old_list):
    tech = Technician.from_dict({"tech_id": "T9", "name": "Ghost", "workstation_ids": old_list})
    assert (tech.tech_id, tech.name) == ("T9", "Ghost") and not hasattr(tech, "workstation_ids")


# --- who gets a call ---

@pytest.fixture(params=[1, 2, 4, 10, "real"], ids=["1-tech", "2-techs", "4-techs", "10-techs", "real-staff"])
def roster(request, make_team, real_technicians):
    """Teams of several sizes, and whoever is in data/technicians.jsonl right now:
    the assignment rules must hold for any number of technicians."""
    return real_technicians if request.param == "real" else make_team(request.param)


@pytest.fixture
def roster_desk(make_desk, roster):
    return make_desk(roster)


def test_everyone_takes_every_kind_of_call(roster_desk, roster, alerts):
    # no history: the first N calls go to N different people, whatever the device
    faults = [alerts.camera, alerts.microphone] * len(roster)
    taken = [roster_desk.open_request_for_alert(fault()).assigned_tech_id for fault in faults[:len(roster)]]
    assert sorted(taken) == sorted(roster)


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
    # B is busy, but B and C have had fewer calls than A, so the draw is between them
    assert take_calls(picked_desk, alerts, 1) == ["B"]
    assert picker.offered[-1] == ["B", "C"]


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


def test_the_default_desk_picks_with_pythons_random(roster_desk, roster, alerts):
    with mock.patch("hardware_monitor.maintenance.random.choice", side_effect=lambda options: options[-1]) as choice:
        request = roster_desk.open_request_for_alert(alerts.camera())

    (offered,), _ = choice.call_args
    assert [t.tech_id for t in offered] == list(roster)  # nobody has a call yet: everyone is in the draw
    assert request.assigned_tech_id == list(roster)[-1]


def test_the_dashboards_own_machine_spreads_calls_to_everyone(roster_desk, roster, alerts):
    # the live dashboard's machine isn't in sample_data.jsonl; its camera and mic
    # faults come one at a time and clear quickly. 2 x N calls: 2 each
    faults = [lambda: alerts.camera(workstation_id="Aliza-HomePC"),
              lambda: alerts.microphone(workstation_id="Aliza-HomePC")]
    taken = []
    for n in range(2 * len(roster)):
        taken += take_calls(roster_desk, alerts, 1, make=faults[n % 2])
    assert Counter(taken) == {tech_id: 2 for tech_id in roster}


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
