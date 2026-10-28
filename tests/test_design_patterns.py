"""Design patterns: the Factory (factory.py, reached through
HardwareComponent.from_dict) and the Strategy (assignment.py: how the
maintenance desk picks a technician). Each pattern is tested on its own
implementations and on the alternatives it allows; where each lives is
checked in test_pattern_modules.py."""

from datetime import datetime, timedelta

import pytest

from hardware_monitor.assignment import (
    AssignmentStrategy,
    FewestCallsStrategy,
    LeastBusyStrategy,
    RoundRobinStrategy,
)
from hardware_monitor.models import Camera, HardwareComponent, MaintenanceRequest, Microphone, Technician, Workstation

from fake_random import LikeliestPick


# --- Factory: HardwareComponent.from_dict builds the right product ---

class Projector(HardwareComponent):
    """A product the factory has never heard of, added only by subclassing."""
    RECORD_FIELDS = ("lumens",)

    def __init__(self, component_id, name, workstation_id, connection_status="ACTIVE",
                 lumens=3000, installed_on=None):
        super().__init__(component_id, name, workstation_id, connection_status, installed_on)
        self.lumens = lumens

    def run_diagnostics(self):
        return self.lumens >= 1000

    def alert_priority(self):
        return 5


def record(component_type, **fields):
    return {"component_type": component_type, "component_id": f"{component_type[:3].upper()}-1",
            "workstation_id": "WS-1", **fields}


@pytest.mark.parametrize("component_type, product", [
    ("Camera", Camera),
    ("camera", Camera),
    ("  MICROPHONE ", Microphone),
])
def test_factory_picks_the_product_from_the_record_type(component_type, product):
    component = HardwareComponent.from_dict(record(component_type))
    assert type(component) is product
    assert isinstance(component, HardwareComponent)


def test_factory_passes_each_product_only_its_own_fields():
    fields = {"resolution": "1280x720", "camera_index": 2, "sample_rate": 16000, "sensitivity_db": -30}
    camera = HardwareComponent.from_dict(record("Camera", **fields))
    microphone = HardwareComponent.from_dict(record("Microphone", **fields))

    assert (camera.resolution, camera.camera_index) == ("1280x720", 2)
    assert not hasattr(camera, "sample_rate")
    assert (microphone.sample_rate, microphone.sensitivity_db) == (16000, -30)
    assert not hasattr(microphone, "resolution")


def test_each_product_validates_its_own_fields():
    # the same bad value is an error for the product that uses it and ignored by the other
    with pytest.raises(ValueError, match="resolution"):
        HardwareComponent.from_dict(record("Camera", resolution=1080))
    assert type(HardwareComponent.from_dict(record("Microphone", resolution=1080))) is Microphone

    with pytest.raises(ValueError, match="sample_rate"):
        HardwareComponent.from_dict(record("Microphone", sample_rate="fast"))
    assert type(HardwareComponent.from_dict(record("Camera", sample_rate="fast"))) is Camera


def test_factory_and_direct_construction_build_the_same_object():
    via_base = HardwareComponent.from_dict(record("Camera", name="Desk cam", resolution="1280x720",
                                                  installed_on="2022-06-01"))
    via_subclass = Camera.from_dict(record("Camera", name="Desk cam", resolution="1280x720",
                                           installed_on="2022-06-01"))
    direct = Camera("CAM-1", "Desk cam", "WS-1", resolution="1280x720", installed_on="2022-06-01")

    for built in (via_base, via_subclass):
        assert vars(built) == vars(direct)


def test_subclass_factory_builds_only_its_own_type():
    assert type(Microphone.from_dict(record("Microphone"))) is Microphone
    with pytest.raises(ValueError, match="got a 'Camera' record"):
        Microphone.from_dict(record("Camera"))


def test_factory_supports_a_new_product_without_changes():
    projector = HardwareComponent.from_dict(record("Projector", lumens=500))

    assert type(projector) is Projector and projector.lumens == 500
    assert projector.run_diagnostics() is False


def test_unknown_product_lists_the_available_types():
    with pytest.raises(ValueError) as error:
        HardwareComponent.from_dict(record("Speaker"))
    for known in ("camera", "microphone", "projector"):
        assert known in str(error.value)


def test_workstation_and_technician_factories_fill_defaults():
    workstation = Workstation.from_dict({"workstation_id": "WS-9"})
    assert (workstation.name, workstation.location, workstation.components) == ("WS-9", "", [])

    technician = Technician.from_dict({"tech_id": "T9", "name": "Dana"})
    assert technician.assigned_requests == [] and not hasattr(technician, "workstation_ids")


# --- Strategy: which technician gets the call ---
# The desk hands every technician to the strategy, with when each was last
# given a call and how many calls each has saved in the log. The default,
# FewestCallsStrategy, draws at random between everyone, favouring fewer calls;
# LeastBusyStrategy and RoundRobinStrategy are alternatives.

def technician(tech_id, open_calls=0):
    tech = Technician(tech_id, tech_id)
    tech.assigned_requests = [MaintenanceRequest(f"MR-{tech_id}-{n}", n, 1) for n in range(open_calls)]
    return tech


@pytest.fixture
def three_ways():
    """C1: 2 open calls, 2 saved, last assigned long ago.
    C2: no open calls but 5 saved, just assigned.
    The three strategies each pick on a different rule here."""
    now = datetime.now()
    candidates = [technician("C1", open_calls=2), technician("C2")]
    last_assigned_at = {"C1": now - timedelta(hours=3), "C2": now}
    calls_taken = {"C1": 2, "C2": 5}
    return candidates, last_assigned_at, calls_taken


def test_strategy_interface_cannot_be_used_directly():
    # Python 3.11 says "with abstract method choose", 3.12+ "abstract method 'choose'"
    with pytest.raises(TypeError, match=r"abstract method.*choose"):
        AssignmentStrategy()


def test_fewest_calls_picks_fewest_saved_even_if_busy(three_ways):
    assert FewestCallsStrategy(rng=LikeliestPick()).choose(*three_ways).tech_id == "C1"  # 2 saved vs 5


def test_least_busy_picks_fewest_open_calls(three_ways):
    assert LeastBusyStrategy().choose(*three_ways).tech_id == "C2"  # 0 open vs 2


def test_round_robin_picks_longest_waiting(three_ways):
    assert RoundRobinStrategy().choose(*three_ways).tech_id == "C1"  # waited 3 hours


@pytest.mark.parametrize("strategy", [LeastBusyStrategy(), RoundRobinStrategy()], ids=["least-busy", "round-robin"])
def test_the_alternatives_prefer_a_never_assigned_technician(strategy):
    candidates = [technician("C1"), technician("C2")]
    assert strategy.choose(candidates, {"C1": datetime.now()}, {}).tech_id == "C2"


@pytest.mark.parametrize("strategy", [LeastBusyStrategy(), RoundRobinStrategy()], ids=["least-busy", "round-robin"])
def test_resolved_calls_do_not_count_as_load(strategy):
    done = technician("C1", open_calls=1)
    done.assigned_requests[0].resolve()
    candidates = [done, technician("C2")]
    assert strategy.choose(candidates, {}, {}).tech_id == "C1"  # tie: both free, first candidate wins


@pytest.fixture
def pair():
    """Two technicians, C1 and C2."""
    return {tid: Technician(tid, tid) for tid in ("C1", "C2")}


def test_desk_uses_fewest_calls_by_default(make_desk, pair):
    assert isinstance(make_desk(pair).assignment_strategy, FewestCallsStrategy)


@pytest.mark.parametrize("strategy, third_call_goes_to", [
    (FewestCallsStrategy(rng=LikeliestPick()), "C1"),  # 1 call each: equally likely, here the first
    (LeastBusyStrategy(), "C2"),                   # C2's call was resolved, so C2 is the free one
    (RoundRobinStrategy(), "C1"),                  # C1 was assigned longest ago, so it is C1's turn
], ids=["fewest-calls", "least-busy", "round-robin"])
def test_swapping_the_strategy_changes_who_gets_the_call(make_desk, pair, strategy, third_call_goes_to, alerts):
    desk = make_desk(pair, assignment_strategy=strategy)
    first = desk.open_request_for_alert(alerts.camera())
    second = desk.open_request_for_alert(alerts.camera())
    desk.resolve_request(second.request_id)

    assert (first.assigned_tech_id, second.assigned_tech_id) == ("C1", "C2")
    assert desk.open_request_for_alert(alerts.camera()).assigned_tech_id == third_call_goes_to


def test_strategy_can_be_changed_on_a_running_desk(make_desk, pair, alerts):
    # the first two calls must go to C1 then C2: with the default's real random
    # draw the second could go to C1 again, so use the likeliest pick
    desk = make_desk(pair, assignment_strategy=FewestCallsStrategy(rng=LikeliestPick()))
    first = desk.open_request_for_alert(alerts.camera())
    second = desk.open_request_for_alert(alerts.camera())
    desk.resolve_request(second.request_id)

    assert (first.assigned_tech_id, second.assigned_tech_id) == ("C1", "C2")

    desk.assignment_strategy = RoundRobinStrategy()
    assert desk.open_request_for_alert(alerts.camera()).assigned_tech_id == first.assigned_tech_id


def test_desk_hands_a_custom_strategy_every_technician_and_their_calls(technicians, alerts, make_desk):
    class RecordingStrategy(AssignmentStrategy):
        def __init__(self):
            self.seen = []

        def choose(self, candidates, last_assigned_at, calls_taken):
            self.seen.append((list(candidates), dict(calls_taken)))
            return candidates[-1]

    strategy = RecordingStrategy()
    desk = make_desk(assignment_strategy=strategy)
    first = desk.open_request_for_alert(alerts.camera())
    desk.open_request_for_alert(alerts.microphone())

    (candidates, calls_before), (_, calls_after) = strategy.seen
    # everyone, whatever the call: no specialties and no workstation lists
    assert candidates == list(technicians.values())
    assert first.assigned_tech_id == candidates[-1].tech_id  # the desk used the strategy's choice
    assert sum(calls_before.values()) == 0
    assert calls_after.get(first.assigned_tech_id) == 1 and sum(calls_after.values()) == 1
