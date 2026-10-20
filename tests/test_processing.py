import json
from datetime import datetime, timedelta

from hardware_monitor.processing import Alert, AlertManager, run_diagnostics_and_log_alerts


def make_alert(priority, error_type="CAMERA_FAILURE", ws="WS-101", **kwargs):
    return Alert("CAM-101", ws, error_type, "test", priority=priority, **kwargs)


def test_dispatch_sends_most_urgent_first():
    manager = AlertManager()
    for priority in (2, 1, 2):
        manager.log_alert(make_alert(priority), persist=False)

    first, second, third = (manager.dispatch_next() for _ in range(3))
    assert first.priority == 1
    assert second.alert_id < third.alert_id  # ties keep arrival order
    assert manager.dispatch_next() is None


def test_intake_acknowledges_in_arrival_order():
    manager = AlertManager()
    routine = manager.log_alert(make_alert(4), persist=False)
    urgent = manager.log_alert(make_alert(1), persist=False)

    assert manager.acknowledge_next() is routine
    assert manager.unacknowledged_alert_ids() == {urgent.alert_id}
    assert manager.acknowledge_next() is urgent
    assert manager.acknowledge_next() is None
    assert manager.unacknowledged_alert_ids() == set()


def test_grouping_and_sets():
    manager = AlertManager()
    manager.log_alert(make_alert(1, "CAMERA_FAILURE", "WS-101"), persist=False)
    manager.log_alert(make_alert(1, "CAMERA_FAILURE", "WS-102"), persist=False)
    manager.log_alert(make_alert(3, "MICROPHONE_FAILURE", "WS-102", resolved=True), persist=False)

    assert {ws: len(alerts) for ws, alerts in manager.group_by_workstation().items()} == {"WS-101": 1, "WS-102": 2}
    assert manager.unique_error_types() == {"CAMERA_FAILURE", "MICROPHONE_FAILURE"}
    assert manager.workstations_without_audio_and_video() == {"WS-102"}
    assert len(manager.unresolved_alerts()) == 2


def test_sorted_by_priority_breaks_ties_by_timestamp():
    now = datetime.now()
    manager = AlertManager()
    late = manager.log_alert(make_alert(3, timestamp=now), persist=False)
    early = manager.log_alert(make_alert(3, timestamp=now - timedelta(minutes=5)), persist=False)
    urgent = manager.log_alert(make_alert(1, timestamp=now), persist=False)

    assert manager.sorted_by_priority() == [urgent, early, late]
    assert manager.most_urgent_and_rest() == (urgent, [early, late])
    assert AlertManager().most_urgent_and_rest() == (None, [])


def test_acknowledge_and_unacknowledge():
    manager = AlertManager()
    manager.acknowledge_alert(7)
    manager.unacknowledge_alert(7)
    manager.unacknowledge_alert(99)  # no-op
    assert manager.acknowledged_alert_ids == set()


def test_log_alert_persists_to_log(tmp_path):
    log_path = tmp_path / "history.jsonl"
    manager = AlertManager(log_path=str(log_path))
    alert = manager.log_alert(make_alert(1))
    with open(log_path, encoding="utf-8") as log_file:
        records = [json.loads(line) for line in log_file]
    assert records == [alert.to_dict()]


def test_diagnostics_on_sample_data_raise_ranked_alerts(workstations, tmp_path):
    manager = AlertManager(log_path=str(tmp_path / "history.jsonl"))
    run_diagnostics_and_log_alerts(workstations, manager)

    assert {(a.component_id, a.priority) for a in manager.alert_history} == {
        ("CAM-103", 2),  # bad resolution: fixable remotely
        ("CAM-104", 1),  # camera lost
        ("CAM-107", 1),
        ("MIC-107", 3),  # microphone lost
        ("MIC-105", 4),  # sample rate too low
    }
    assert manager.workstations_without_audio_and_video() == {"WS-107"}
    assert [manager.dispatch_next().priority for _ in range(5)] == [1, 1, 2, 3, 4]
