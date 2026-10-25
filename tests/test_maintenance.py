"""The maintenance desk. Who gets a call: there are no specialists, so every
technician can take any open call, of any kind, on any workstation. Each
call is a random draw between every technician, weighted towards those with
fewer calls saved in the maintenance log (every call they were ever given,
resolved ones and earlier runs included): someone k calls ahead of the
least-loaded technician weighs 1 / (1 + k). Anyone can still get the call.

The default strategy is FewestCallsStrategy(rng=random): rng.choices() gets
every technician and their weights. Tests that need to know who is picked
pass a `picker` (fake_random.LikeliestPick), which records each draw and
takes the likeliest technician; statistical tests use a seeded Random."""

import csv
import json
import random
from collections import Counter
from unittest import mock

import pytest

from hardware_monitor.maintenance import FewestCallsStrategy
from hardware_monitor.models import Technician

from conftest import BAD_FIELDS, TECHNICIANS_PATH
from fake_random import LikeliestPick


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
    """Stands in for random: records each draw and takes the likeliest technician."""
    return LikeliestPick()


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


def weights(calls):
    """The weights FewestCallsStrategy gives for these saved-call counts."""
    picker = LikeliestPick()
    team = [Technician(tech_id, tech_id) for tech_id in calls]
    FewestCallsStrategy(rng=picker).choose(team, {}, calls)
    return picker.draws[0]


def test_the_weights_favour_fewer_calls():
    # someone k calls ahead of the least-loaded technician weighs 1 / (1 + k)
    assert weights({"A": 0, "B": 1, "C": 3}) == {"A": 1, "B": 1 / 2, "C": 1 / 4}


def test_the_bias_depends_on_the_gap_not_the_totals():
    # after months of calls (100 vs 103) the bias is as strong as on day one (0 vs 3)
    assert weights({"A": 100, "B": 101, "C": 103}) == weights({"A": 0, "B": 1, "C": 3})


def test_everyone_is_in_every_draw(picked_desk, picker, alerts):
    take_calls(picked_desk, alerts, 6)
    for draw in picker.draws:
        assert set(draw) == {"A", "B", "C"} and all(weight > 0 for weight in draw.values())


def test_the_technicians_with_the_fewest_calls_are_favoured(picked_desk, picker, alerts):
    take_calls(picked_desk, alerts, 4)
    assert picker.favoured == [
        ["A", "B", "C"],   # nobody has a call yet: all three equally
        ["B", "C"],        # A has 1
        ["C"],             # A and B have 1: C is the only one at 0
        ["A", "B", "C"],   # all have 1 again
    ]


def test_with_the_likeliest_pick_the_first_calls_go_to_different_people(make_desk, roster, alerts):
    desk = make_desk(roster, assignment_strategy=FewestCallsStrategy(rng=LikeliestPick()))
    faults = [alerts.camera, alerts.microphone] * len(roster)
    taken = [desk.open_request_for_alert(fault()).assigned_tech_id for fault in faults[:len(roster)]]
    assert sorted(taken) == sorted(roster)  # whatever the device


def test_resolved_calls_still_count(picked_desk, picker, alerts):
    # fewer calls saved, not fewer open: A and B are free right now, but once C
    # has had a call too, all three have 1 and are favoured equally
    take_calls(picked_desk, alerts, 2)                  # A, B: resolved
    take_calls(picked_desk, alerts, 1, resolve=False)   # C: still open
    take_calls(picked_desk, alerts, 1)
    assert picker.favoured[-1] == ["A", "B", "C"]


def test_a_busy_technician_with_fewer_calls_is_favoured(picked_desk, picker, alerts):
    assert take_calls(picked_desk, alerts, 1) == ["A"]                 # A: 1, resolved
    assert take_calls(picked_desk, alerts, 1, resolve=False) == ["B"]  # B: 1, still open
    assert take_calls(picked_desk, alerts, 2) == ["C", "A"]            # C: 1; then A: 2
    # B is busy, but B and C have had fewer calls than A
    assert take_calls(picked_desk, alerts, 1) == ["B"]
    assert picker.favoured[-1] == ["B", "C"]


DRAWS = 6000


def picks(calls, seed=2026):
    """How often each technician is picked, over DRAWS real random draws."""
    strategy = FewestCallsStrategy(rng=random.Random(seed))
    team = [Technician(tech_id, tech_id) for tech_id in ("A", "B", "C")]
    return Counter(strategy.choose(team, {}, calls).tech_id for _ in range(DRAWS))


def test_anyone_can_still_get_the_call():
    # C has 20 more calls than A, yet still gets some
    assert set(picks({"A": 0, "B": 5, "C": 20})) == {"A", "B", "C"}


def test_fewer_calls_means_picked_more_often():
    counts = picks({"A": 0, "B": 1, "C": 3})
    total_weight = 1 + 1 / 2 + 1 / 4
    for tech_id, weight in (("A", 1), ("B", 1 / 2), ("C", 1 / 4)):
        assert counts[tech_id] / DRAWS == pytest.approx(weight / total_weight, abs=0.03)
    assert counts["A"] > counts["B"] > counts["C"]


def test_equal_calls_give_equal_chances():
    counts = picks({"A": 3, "B": 3, "C": 3})
    assert all(counts[t] / DRAWS == pytest.approx(1 / 3, abs=0.03) for t in "ABC")


def test_a_technician_with_no_saved_calls_counts_as_zero():
    counts = picks({"A": 1, "B": 1})  # C has none saved: the fewest, so the most likely
    assert counts["C"] / DRAWS == pytest.approx(0.5, abs=0.03)


def test_the_default_desk_draws_with_pythons_random(roster_desk, roster, alerts):
    with mock.patch("hardware_monitor.maintenance.random.choices",
                    side_effect=lambda population, weights, k=1: [population[-1]]) as choices:
        request = roster_desk.open_request_for_alert(alerts.camera())

    (population, drawn_weights), _ = choices.call_args
    assert [t.tech_id for t in population] == list(roster)  # everyone is in the draw
    assert set(drawn_weights) == {1}                        # nobody has a call yet: equal weights
    assert request.assigned_tech_id == list(roster)[-1]


def test_over_many_calls_the_work_evens_out(roster):
    # 30 calls per technician, one at a time, each counted as it is given: with
    # real random picks, everyone gets calls and nobody falls far behind
    strategy = FewestCallsStrategy(rng=random.Random(7))
    candidates, calls_taken = list(roster.values()), Counter()
    for _ in range(30 * len(roster)):
        calls_taken[strategy.choose(candidates, {}, calls_taken).tech_id] += 1
    assert set(calls_taken) == set(roster)                               # everyone got calls
    assert max(calls_taken.values()) - min(calls_taken.values()) <= 6    # about 30 each, none far off


def test_calls_saved_by_an_earlier_run_count(make_desk, team, picker, alerts, saved_log):
    saved_log("A", "A", "B")                          # an earlier run: A had 2 calls, B had 1
    desk = make_desk(team, assignment_strategy=FewestCallsStrategy(rng=picker))
    assert take_calls(desk, alerts, 3) == ["C", "B", "C"]
    assert picker.favoured == [["C"], ["B", "C"], ["C"]]


def test_a_call_logged_twice_counts_once(make_desk, team, picker, make_call, write_jsonl, alerts):
    # every call is in the log once when opened and again when resolved; A's
    # call must count once, so all three have 1 and are favoured equally
    write_jsonl("logs/maintenance_requests.jsonl",
                make_call(1, "CAMERA_FAILURE", "A", "IN_PROGRESS"),
                make_call(1, "CAMERA_FAILURE", "A", "RESOLVED"),
                make_call(2, "CAMERA_FAILURE", "B", "RESOLVED"),
                make_call(3, "CAMERA_FAILURE", "C", "RESOLVED"))
    desk = make_desk(team, assignment_strategy=FewestCallsStrategy(rng=picker))
    take_calls(desk, alerts, 1)
    assert picker.favoured == [["A", "B", "C"]]


def test_open_calls_saved_by_an_earlier_run_count_too(make_desk, team, picker, alerts, saved_log):
    saved_log("A", "B", status="IN_PROGRESS")
    desk = make_desk(team, assignment_strategy=FewestCallsStrategy(rng=picker))
    assert take_calls(desk, alerts, 1) == ["C"]


def test_a_technician_who_left_doesnt_affect_the_rest(make_desk, team, picker, alerts, saved_log):
    saved_log("T42", "T42", "T42")                    # in the log, no longer in technicians.jsonl
    desk = make_desk(team, assignment_strategy=FewestCallsStrategy(rng=picker))
    assert sorted(take_calls(desk, alerts, 3)) == ["A", "B", "C"]


@pytest.mark.parametrize("bad_field", BAD_FIELDS)
def test_the_desk_starts_on_a_log_with_a_wrongly_typed_call(bad_field, make_desk, team, picker, make_call,
                                                            write_jsonl, alerts, capsys):
    write_jsonl("logs/maintenance_requests.jsonl",
                make_call(1, "CAMERA_FAILURE", "A", "RESOLVED"),
                {**make_call(2, "CAMERA_FAILURE", "B", "IN_PROGRESS"), **bad_field})  # line 2

    desk = make_desk(team, assignment_strategy=FewestCallsStrategy(rng=picker))  # must not crash

    output = capsys.readouterr().out
    assert "skipping" in output and "line 2" in output
    assert desk.open_requests() == []        # the unreadable call isn't restored
    take_calls(desk, alerts, 1)
    assert picker.favoured == [["B", "C"]]   # only A's readable call counts


def test_the_number_of_a_skipped_call_is_never_reused(make_desk, make_call, write_jsonl, alerts):
    write_jsonl("logs/maintenance_requests.jsonl",
                make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED"),
                {**make_call(2, "CAMERA_FAILURE", "T2", "IN_PROGRESS"), "opened_at": 5})
    # MR-0002 is already in the log, readable or not: a second MR-0002 would mix up the two calls
    assert make_desk().open_request_for_alert(alerts.camera()).request_id == "MR-0003"


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
