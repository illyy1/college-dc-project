"""Design patterns: the Factory (from_dict class methods) and the Strategy
(how the maintenance desk picks a technician). Each pattern is tested on its
own implementations and on the alternatives it allows."""

from datetime import datetime, timedelta

import pytest

from hardware_monitor.maintenance import AssignmentStrategy, LeastBusyStrategy, RoundRobinStrategy
from hardware_monitor.models import Camera, HardwareComponent, MaintenanceRequest, Microphone, Technician, Workstation


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

    technician = Technician.from_dict({"tech_id": "T9", "name": "Dana", "specialty": "General"})
    assert technician.workstation_ids == () and technician.assigned_requests == []


# --- Strategy: which eligible technician gets the call ---

def technician(tech_id, open_calls=0, specialty="Camera"):
    tech = Technician(tech_id, tech_id, specialty, workstation_ids=["WS-104"])
    tech.assigned_requests = [MaintenanceRequest(f"MR-{tech_id}-{n}", n, 1) for n in range(open_calls)]
    return tech


@pytest.fixture
def busy_but_waiting():
    """C1 has two open calls but was last assigned long ago; C2 has none but
    was just assigned. The two strategies disagree on this case."""
    now = datetime.now()
    candidates = [technician("C1", open_calls=2), technician("C2")]
    last_assigned_at = {"C1": now - timedelta(hours=3), "C2": now}
    return candidates, last_assigned_at


def test_strategy_interface_cannot_be_used_directly():
    # Python 3.11 says "with abstract method choose", 3.12+ "abstract method 'choose'"
    with pytest.raises(TypeError, match=r"abstract method.*choose"):
        AssignmentStrategy()


def test_least_busy_picks_fewest_open_calls(busy_but_waiting):
    candidates, last_assigned_at = busy_but_waiting
    assert LeastBusyStrategy().choose(candidates, last_assigned_at).tech_id == "C2"


def test_round_robin_picks_longest_waiting(busy_but_waiting):
    candidates, last_assigned_at = busy_but_waiting
    assert RoundRobinStrategy().choose(candidates, last_assigned_at).tech_id == "C1"


@pytest.mark.parametrize("strategy", [LeastBusyStrategy(), RoundRobinStrategy()], ids=["least-busy", "round-robin"])
def test_both_strategies_prefer_a_never_assigned_technician(strategy):
    candidates = [technician("C1"), technician("C2")]
    assert strategy.choose(candidates, {"C1": datetime.now()}).tech_id == "C2"


@pytest.mark.parametrize("strategy", [LeastBusyStrategy(), RoundRobinStrategy()], ids=["least-busy", "round-robin"])
def test_resolved_calls_do_not_count_as_load(strategy):
    done = technician("C1", open_calls=1)
    done.assigned_requests[0].resolve()
    candidates = [done, technician("C2")]
    assert strategy.choose(candidates, {}).tech_id == "C1"  # tie: both free, first candidate wins


@pytest.fixture
def camera_team():
    """Two camera technicians, C1 and C2, both covering WS-104."""
    return {tid: Technician(tid, tid, "Camera", workstation_ids=["WS-104"]) for tid in ("C1", "C2")}


def test_desk_uses_least_busy_by_default(make_desk, camera_team):
    assert isinstance(make_desk(camera_team).assignment_strategy, LeastBusyStrategy)


@pytest.mark.parametrize("strategy, third_call_goes_to", [
    (LeastBusyStrategy(), "C2"),   # C2's call was resolved, so C2 is the free one
    (RoundRobinStrategy(), "C1"),  # C1 was assigned longest ago, so it is C1's turn
], ids=["least-busy", "round-robin"])
def test_swapping_the_strategy_changes_who_gets_the_call(make_desk, camera_team, strategy, third_call_goes_to, alerts):
    desk = make_desk(camera_team, assignment_strategy=strategy)
    first = desk.open_request_for_alert(alerts.camera())
    second = desk.open_request_for_alert(alerts.camera())
    desk.resolve_request(second.request_id)

    assert (first.assigned_tech_id, second.assigned_tech_id) == ("C1", "C2")
    assert desk.open_request_for_alert(alerts.camera()).assigned_tech_id == third_call_goes_to


def test_strategy_can_be_changed_on_a_running_desk(make_desk, camera_team, alerts):
    desk = make_desk(camera_team)
    desk.open_request_for_alert(alerts.camera())
    second = desk.open_request_for_alert(alerts.camera())
    desk.resolve_request(second.request_id)

    desk.assignment_strategy = RoundRobinStrategy()
    assert desk.open_request_for_alert(alerts.camera()).assigned_tech_id == "C1"


def test_desk_hands_a_custom_strategy_only_eligible_candidates(technicians, alerts, make_desk):
    class RecordingStrategy(AssignmentStrategy):
        def __init__(self):
            self.seen = []

        def choose(self, candidates, last_assigned_at):
            self.seen.append(list(candidates))
            return candidates[-1]

    strategy = RecordingStrategy()
    desk = make_desk(assignment_strategy=strategy)
    request = desk.open_request_for_alert(alerts.camera())

    (candidates,) = strategy.seen
    assert candidates == [t for t in technicians.values() if t.specialty == "Camera" and t.covers("WS-104")]
    assert request.assigned_tech_id == candidates[-1].tech_id  # the desk used the strategy's choice
