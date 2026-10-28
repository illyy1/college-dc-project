"""TDD specification: the Factory is injected, like the Strategy.

repository.load_workstations_from_jsonl(path, component_factory=...) builds
each line's component with the factory it is given instead of calling a
fixed one. The default is factory.create_component, so every existing
caller (demo.py, async_processing.py) works unchanged. Whatever the injected
factory raises as ValueError is still reported as a DataFileError naming
the file and line, and the loader no longer calls
HardwareComponent.from_dict itself.

The functional need shown here: a data source that names its components
differently (an older export calls a camera a "webcam") is loaded by
passing in a factory that understands it, with no change to the loader."""

import inspect
import os

import pytest

from conftest import PROJECT_ROOT
from hardware_monitor import factory
from hardware_monitor.models import Camera, Microphone
from hardware_monitor.repository import DataFileError, load_workstations_from_jsonl


def line(component_type, component_id, workstation_id="WS-1", **fields):
    return {"workstation_id": workstation_id, "workstation_name": "Lab PC", "location": "Lab",
            "component_type": component_type, "component_id": component_id, **fields}


class RecordingFactory:
    """Wraps the real factory and records every record it was given."""

    def __init__(self):
        self.records, self.built = [], []

    def __call__(self, record):
        component = factory.create_component(record)
        self.records.append(record)
        self.built.append(component)
        return component


LEGACY_NAMES = {"webcam": "camera", "mic": "microphone"}


def legacy_factory(record):
    """A factory for an older export that says "webcam" and "mic"."""
    kind = record["component_type"].strip().lower()
    return factory.create_component({**record, "component_type": LEGACY_NAMES.get(kind, kind)})


def test_the_injected_factory_builds_every_component(write_jsonl):
    path = write_jsonl("data.jsonl", line("camera", "CAM-1"), line("microphone", "MIC-1"),
                       line("camera", "CAM-2", workstation_id="WS-2"))
    recording = RecordingFactory()
    workstations = load_workstations_from_jsonl(path, component_factory=recording)

    assert [r["component_id"] for r in recording.records] == ["CAM-1", "MIC-1", "CAM-2"]
    loaded = [c for ws in workstations for c in ws.components]
    assert len(loaded) == 3 and all(any(c is b for b in recording.built) for c in loaded)  # its objects, not copies


def test_a_factory_for_another_data_source_works_without_changing_the_loader(write_jsonl):
    path = write_jsonl("old_export.jsonl", line("webcam", "CAM-1"), line("Mic", "MIC-1"))

    (workstation,) = load_workstations_from_jsonl(path, component_factory=legacy_factory)
    assert [type(c) for c in workstation.components] == [Camera, Microphone]

    with pytest.raises(DataFileError, match="line 1: Unknown component_type 'webcam'"):
        load_workstations_from_jsonl(path)  # the default factory doesn't know those names


def test_an_injected_factory_s_error_names_the_file_and_line(write_jsonl):
    path = write_jsonl("data.jsonl", line("camera", "CAM-1"), line("camera", "CAM-2"))

    def refuses_the_second(record):
        if record["component_id"] == "CAM-2":
            raise ValueError("CAM-2 is on the recall list")
        return factory.create_component(record)

    with pytest.raises(DataFileError, match=r"data\.jsonl line 2: CAM-2 is on the recall list"):
        load_workstations_from_jsonl(path, component_factory=refuses_the_second)


def test_the_default_is_the_project_s_factory():
    parameter = inspect.signature(load_workstations_from_jsonl).parameters["component_factory"]
    assert parameter.default is factory.create_component


def test_the_default_still_loads_the_sample_data():
    workstations = load_workstations_from_jsonl(os.path.join(PROJECT_ROOT, "data", "sample_data.jsonl"))
    assert workstations and all(ws.components for ws in workstations)


def test_the_loader_no_longer_calls_a_fixed_factory():
    source = inspect.getsource(load_workstations_from_jsonl)
    assert "HardwareComponent.from_dict" not in source and "create_component(" not in source
