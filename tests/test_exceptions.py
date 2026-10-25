"""The system raises the right exception, with a useful message, for bad
data and critical failures, and a failure never leaves things half-done:
no diagnostic port stays claimed and no lock stays held."""

from unittest import mock

import pytest

import hardware_monitor
from hardware_monitor.context_managers import DiagnosticConnection
from hardware_monitor.maintenance import AssignmentStrategy
from hardware_monitor.models import Camera, HardwareComponent
from hardware_monitor.processing import AlertManager
from hardware_monitor.repository import (
    load_maintenance_history,
    load_technicians_from_jsonl,
    load_workstations_from_jsonl,
)


# --- critical failures release what they hold ---

def test_a_diagnostic_session_that_fails_to_start_releases_its_port():
    camera = Camera("C1", "Cam", "WS-1")
    with mock.patch.object(camera, "begin_test", side_effect=RuntimeError("probe is locked")):
        with pytest.raises(RuntimeError, match="probe is locked"):
            with DiagnosticConnection(camera, verbose=False):
                pytest.fail("the with block must not run when the session could not start")

    assert DiagnosticConnection.available_ports() == list(DiagnosticConnection.PORT_POOL)
    assert camera.connection_status == "ACTIVE"


def test_a_failure_inside_the_session_is_reraised_after_cleanup():
    camera = Camera("C1", "Cam", "WS-1")
    with pytest.raises(ZeroDivisionError):
        with DiagnosticConnection(camera, verbose=False):
            1 / 0
    assert camera.connection_status == "ERROR"
    assert DiagnosticConnection.available_ports() == list(DiagnosticConnection.PORT_POOL)


def test_a_failing_assignment_strategy_does_not_leave_the_desk_locked(alerts, make_desk):
    class BrokenStrategy(AssignmentStrategy):
        def choose(self, candidates, last_assigned_at, calls_taken):
            raise RuntimeError("strategy crashed")

    desk = make_desk(assignment_strategy=BrokenStrategy())
    with pytest.raises(RuntimeError, match="strategy crashed"):
        desk.open_request_for_alert(alerts.camera())

    assert not desk._lock.locked()
    assert desk.open_requests() == []  # nothing half-opened, and the desk still answers


def test_a_failed_log_write_does_not_leave_the_alert_manager_locked(tmp_path, alerts):
    unwritable = tmp_path / "a_folder"
    unwritable.mkdir()  # a folder where the log file should be: every write fails
    manager = AlertManager(log_path=str(unwritable))

    with pytest.raises(OSError):
        manager.log_alert(alerts.camera())
    assert not manager._lock.locked()

    manager.log_path = str(tmp_path / "history.jsonl")
    manager.log_alert(alerts.camera())  # works again once the log is writable


# --- missing or broken data files ---

@pytest.mark.parametrize("loader", [load_workstations_from_jsonl, load_technicians_from_jsonl],
                         ids=["workstations", "technicians"])
def test_a_missing_data_file_stops_loading(tmp_path, loader):
    missing = tmp_path / "no_such_file.jsonl"
    with pytest.raises(FileNotFoundError) as error:
        loader(str(missing))
    assert error.value.filename == str(missing)


def test_a_missing_maintenance_log_is_not_an_error(tmp_path):
    # unlike the data files, the log simply doesn't exist before the first call
    assert load_maintenance_history(str(tmp_path / "not_yet_created.jsonl")) == []


def test_bad_json_raises_a_plain_value_error_without_parser_internals(write_jsonl):
    path = write_jsonl("data.jsonl", '{"component_id": "CAM-1",')

    with pytest.raises(ValueError) as error:
        load_workstations_from_jsonl(path)

    assert error.type is ValueError  # not json.JSONDecodeError, which is a subclass
    assert str(error.value).startswith(f"{path} line 1: invalid JSON")
    assert error.value.__cause__ is None and error.value.__suppress_context__  # "raise ... from None"


def test_an_impossible_date_is_reported_for_the_component_not_by_date_parsing(write_jsonl):
    path = write_jsonl("data.jsonl", {"component_type": "Camera", "component_id": "CAM-1", "workstation_id": "WS-1",
                                      "installed_on": "2021-02-30"})

    # Python's own wording of the date error varies by version; the prefix is ours
    with pytest.raises(ValueError, match=r"line 1: Component 'CAM-1': .*\bday\b") as error:
        load_workstations_from_jsonl(path)
    assert error.value.__suppress_context__


# --- programming errors ---

def test_an_unknown_package_attribute_raises_attribute_error():
    with pytest.raises(AttributeError, match="module 'hardware_monitor' has no attribute 'NoSuchThing'"):
        hardware_monitor.NoSuchThing


def test_importing_an_unknown_name_from_the_package_fails():
    with pytest.raises(ImportError, match="NoSuchThing"):
        from hardware_monitor import NoSuchThing  # noqa: F401


class LazyComponent(HardwareComponent):
    """Implements the abstract methods by deferring to the base class."""

    def run_diagnostics(self):
        return super().run_diagnostics()

    def alert_priority(self):
        return super().alert_priority()


@pytest.mark.parametrize("method", ["run_diagnostics", "alert_priority"])
def test_the_base_class_refuses_to_do_a_subclass_job(method):
    component = LazyComponent("L1", "Lazy", "WS-1")
    with pytest.raises(NotImplementedError, match=f"Subclasses must implement {method}"):
        getattr(component, method)()
