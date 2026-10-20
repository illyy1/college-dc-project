import pytest

from hardware_monitor.models import (
    Camera,
    Microphone,
    Workstation,
    MaintenanceRequest,
)


def test_sample_data_loads_all_workstations(workstations):
    assert len(workstations) == 8
    assert all(len(ws.components) == 2 for ws in workstations)


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
    with pytest.raises(ValueError):
        cam.connection_status = "BROKEN"


def test_camera_diagnostics():
    assert Camera("C1", "Cam", "WS-1", resolution="1280x720").run_diagnostics() is True
    bad = Camera("C2", "Cam", "WS-1", resolution="garbage")
    assert bad.run_diagnostics() is False
    assert bad.connection_status == "DISCONNECTED"


def test_microphone_diagnostics():
    assert Microphone("M1", "Mic", "WS-1", sample_rate=48000).run_diagnostics() is True
    assert Microphone("M2", "Mic", "WS-1", sample_rate=4000).run_diagnostics() is False
    assert Microphone("M3", "Mic", "WS-1", connection_status="DISCONNECTED").run_diagnostics() is False


def test_workstation_composition():
    ws = Workstation("WS-1", "Desk")
    ws.add_component(Camera("C1", "Cam", "WS-1"))
    ws.add_component(Microphone("M1", "Mic", "WS-1", sample_rate=100))

    ws.components.clear()  # returns a copy, so the workstation is unaffected
    assert len(ws.components) == 2

    assert ws.run_all_diagnostics() == {"C1": True, "M1": False}
    assert ws.remove_component("C1") is True
    assert ws.remove_component("C1") is False
    assert ws.find_component("C1") is None


def test_maintenance_request_lifecycle():
    request = MaintenanceRequest("MR-0001", alert_id=1, priority=2)
    assert request.status == "OPEN"
    request.assign_to("T1")
    assert request.status == "IN_PROGRESS" and request.assigned_tech_id == "T1"
    request.resolve()
    record = request.to_dict()
    assert record["status"] == "RESOLVED"
    assert record["resolved_at"] is not None
    with pytest.raises(ValueError):
        request.status = "DONE"
