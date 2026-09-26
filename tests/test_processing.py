import json
from datetime import datetime, timedelta

from hardware_monitor import processing
from hardware_monitor.processing import Alert, AlertManager


def make_alert(priority, error_type="CAMERA_FAILURE", ws="WS-101", **kwargs):
    return Alert("CAM-101", ws, error_type, "test", priority=priority, **kwargs)


def test_critical_alerts_come_out_by_priority():
    manager = AlertManager()
    for priority in (2, 1, 2):
        manager.log_alert(make_alert(priority), persist=False)

    first, second, third = (manager.process_next_critical() for _ in range(3))
    assert first.priority == 1
    assert second.alert_id < third.alert_id  # ties keep insertion order
    assert manager.process_next_critical() is None


def test_routine_alerts_are_fifo():
    manager = AlertManager()
    a = manager.log_alert(make_alert(4), persist=False)
    b = manager.log_alert(make_alert(5), persist=False)
    assert manager.process_next_maintenance() is a
    assert manager.process_next_maintenance() is b
    assert manager.process_next_maintenance() is None


def test_grouping_and_sets():
    manager = AlertManager()
    manager.log_alert(make_alert(1, "CAMERA_FAILURE", "WS-101"), persist=False)
    manager.log_alert(make_alert(4, "CAMERA_FAILURE", "WS-102"), persist=False)
    manager.log_alert(make_alert(4, "MICROPHONE_SILENCE", "WS-102", resolved=True), persist=False)

    assert {ws: len(alerts) for ws, alerts in manager.group_by_workstation().items()} == {"WS-101": 1, "WS-102": 2}
    assert manager.unique_error_types() == {"CAMERA_FAILURE", "MICROPHONE_SILENCE"}
    assert manager.critical_and_routine_common_error_types() == {"CAMERA_FAILURE"}
    assert len(manager.unresolved_alerts()) == 2


def test_sorted_by_priority_breaks_ties_by_timestamp():
    now = datetime.now()
    manager = AlertManager()
    late = manager.log_alert(make_alert(3, timestamp=now), persist=False)
    early = manager.log_alert(make_alert(3, timestamp=now - timedelta(minutes=5)), persist=False)
    urgent = manager.log_alert(make_alert(1, timestamp=now), persist=False)
    assert manager.sorted_by_priority() == [urgent, early, late]


def test_acknowledge_and_unacknowledge():
    manager = AlertManager()
    manager.acknowledge_alert(7)
    manager.unacknowledge_alert(7)
    manager.unacknowledge_alert(99)  # no-op
    assert manager.acknowledged_alert_ids == set()


def test_log_alert_persists_to_log():
    manager = AlertManager()
    alert = manager.log_alert(make_alert(1))
    with open(processing.LOG_PATH, encoding="utf-8") as log_file:
        records = [json.loads(line) for line in log_file]
    assert records == [alert.to_dict()]


def test_alerts_from_sample_data(workstations):
    """Every non-ACTIVE component in the sample data becomes an alert."""
    manager = AlertManager()
    for ws in workstations:
        for component in ws.components:
            if component.connection_status != "ACTIVE":
                manager.log_alert(
                    Alert(component.component_id, ws.workstation_id,
                          f"{component.component_type.upper()}_{component.connection_status}",
                          "from sample data", priority=1 if component.component_type == "Camera" else 4),
                    persist=False,
                )
    assert len(manager.alert_history) == 5
    critical = [manager.process_next_critical() for _ in range(3)]
    assert all(a.error_type.startswith("CAMERA_") for a in critical)
    assert manager.process_next_critical() is None
    assert len(manager.maintenance_queue) == 2
