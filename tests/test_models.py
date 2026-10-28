import pytest

from hardware_monitor.models import (
    Camera,
    Microphone,
    MaintenanceRequest,
)


def test_the_test_fleet_loads_all_workstations(workstations):
    assert len(workstations) == 8
    assert all(len(ws.components) == 2 for ws in workstations)


def test_the_real_sample_data_is_a_valid_fleet(real_workstations):
    # data/sample_data.jsonl can change, so only what any valid fleet must have
    assert real_workstations, "the sample data has no workstations"
    ids = [c.component_id for ws in real_workstations for c in ws.components]
    assert len(ids) == len(set(ids))  # every component id is unique
    for ws in real_workstations:
        assert ws.components and all(c.workstation_id == ws.workstation_id for c in ws.components)


def test_sample_data_builds_camera_and_microphone(workstations):
    ws = next(w for w in workstations if w.workstation_id == "WS-101")
    cam = ws.find_component("CAM-101")
    mic = ws.find_component("MIC-101")
    assert isinstance(cam, Camera) and cam.resolution == "1920x1080"
    assert isinstance(mic, Microphone) and mic.sample_rate == 44100
    assert ws.find_components_by_type("Camera") == [cam]


def test_connection_status_is_validated_and_normalized():
    cam = Camera("C1", "Cam", "WS-1", connection_status="active")
    assert cam.connection_status == "ACTIVE"
    with pytest.raises(ValueError, match="Invalid connection status 'BROKEN'"):
        cam.connection_status = "BROKEN"
    assert cam.connection_status == "ACTIVE"  # the bad value was not stored


def test_camera_diagnostics():
    assert Camera("C1", "Cam", "WS-1", resolution="1280x720").run_diagnostics() is True
    bad = Camera("C2", "Cam", "WS-1", resolution="garbage")
    assert bad.run_diagnostics() is False
    assert bad.connection_status == "DISCONNECTED"


def test_microphone_diagnostics():
    assert Microphone("M1", "Mic", "WS-1", sample_rate=48000).run_diagnostics() is True
    assert Microphone("M2", "Mic", "WS-1", sample_rate=4000).run_diagnostics() is False
    assert Microphone("M3", "Mic", "WS-1", connection_status="DISCONNECTED").run_diagnostics() is False


def test_workstation_composition(bench_workstation):
    bench_workstation.components.clear()  # returns a copy, so the workstation is unaffected
    assert len(bench_workstation.components) == 3

    assert bench_workstation.run_all_diagnostics() == {"CAM-B1": True, "CAM-B2": False, "MIC-B1": True}
    assert [c.component_id for c in bench_workstation.find_components_by_type("Camera")] == ["CAM-B1", "CAM-B2"]
    assert bench_workstation.remove_component("CAM-B2") is True
    assert bench_workstation.remove_component("CAM-B2") is False
    assert bench_workstation.find_component("CAM-B2") is None


def test_each_test_gets_its_own_bench_workstation(bench_workstation):
    # fixtures are rebuilt for every test, so the removal above left no trace
    assert len(bench_workstation.components) == 3


def test_maintenance_request_lifecycle():
    request = MaintenanceRequest("MR-0001", alert_id=1, priority=2)
    assert request.status == "OPEN"
    request.assign_to("T1")
    assert request.status == "IN_PROGRESS" and request.assigned_tech_id == "T1"
    request.resolve()
    record = request.to_dict()
    assert record["status"] == "RESOLVED"
    assert record["resolved_at"] is not None
    with pytest.raises(ValueError, match="סטטוס לא חוקי: DONE"):  # "illegal status: DONE"
        request.status = "DONE"
    assert request.status == "RESOLVED"


# --- a maintenance call never holds an impossible priority or reopens (Part 1, section 4) ---

@pytest.mark.parametrize("priority", [1, 5])
def test_a_call_accepts_priorities_1_to_5(priority):
    assert MaintenanceRequest("MR-0001", alert_id=1, priority=priority).priority == priority


@pytest.mark.parametrize("priority", [0, 6, -3, 2.5, "1", True, None],
                         ids=["zero", "six", "negative", "fraction", "text", "bool", "none"])
def test_a_call_rejects_an_impossible_priority(priority):
    with pytest.raises(ValueError, match=r"priority must be a whole number from 1 \(most urgent\) to 5"):
        MaintenanceRequest("MR-0001", alert_id=1, priority=priority)


def test_changing_the_priority_is_checked_too():
    request = MaintenanceRequest("MR-0001", alert_id=1, priority=3)
    request.priority = 2  # what Gemini's triage does
    with pytest.raises(ValueError, match="got 0"):
        request.priority = 0
    assert request.priority == 2


def test_a_saved_call_with_an_impossible_priority_is_rejected():
    with pytest.raises(ValueError, match="priority must be a whole number from 1"):
        MaintenanceRequest.from_dict({"request_id": "MR-0001", "priority": 9})


@pytest.mark.parametrize("reopen", [lambda r: setattr(r, "status", "OPEN"),
                                    lambda r: setattr(r, "status", "IN_PROGRESS"),
                                    lambda r: r.assign_to("T2")],
                         ids=["open", "in-progress", "assign"])
def test_a_resolved_call_cannot_be_reopened(reopen):
    request = MaintenanceRequest("MR-0001", alert_id=1, priority=2)
    request.assign_to("T1")
    request.resolve()
    resolved_at = request.resolved_at

    with pytest.raises(ValueError, match="MR-0001 is already resolved"):
        reopen(request)
    assert (request.status, request.resolved_at, request.assigned_tech_id) == ("RESOLVED", resolved_at, "T1")
