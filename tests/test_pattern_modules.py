"""TDD specification: each design pattern lives in its own dedicated module.

- hardware_monitor/assignment.py, the Strategy: how the maintenance desk
  picks a technician. It holds AssignmentStrategy and its three
  implementations (FewestCallsStrategy, LeastBusyStrategy,
  RoundRobinStrategy) and nothing else: no other classes, and nothing
  imported from the rest of the package. maintenance.py defines no
  strategy of its own; it imports them, so `from
  hardware_monitor.maintenance import FewestCallsStrategy` still works.
- hardware_monitor/factory.py, the Factory: create_component(record) picks
  the HardwareComponent subclass named by record["component_type"] (any
  case, spaces ignored) and builds it with that subclass's from_dict;
  component_types() lists the types it knows. A new subclass is picked up
  without changes. An unknown type raises ValueError listing the known
  ones. It imports only models. HardwareComponent.from_dict, called on the
  base class, hands the record to create_component, so existing callers
  keep working; models.py no longer picks the subclass itself.

The patterns' behaviour is tested in tests/test_design_patterns.py; this
file checks where they live and that the old entry points still lead
there."""

import ast
import importlib
import os
from unittest import mock

import pytest

from conftest import PROJECT_ROOT
from hardware_monitor import maintenance, models
from hardware_monitor.models import Camera, HardwareComponent, Microphone

PACKAGE = os.path.join(PROJECT_ROOT, "hardware_monitor")
STRATEGIES = {"AssignmentStrategy", "FewestCallsStrategy", "LeastBusyStrategy", "RoundRobinStrategy"}


def module(name):
    return importlib.import_module(f"hardware_monitor.{name}")


def tree(name):
    with open(os.path.join(PACKAGE, f"{name}.py"), encoding="utf-8") as source:
        return ast.parse(source.read())


def classes_in(name):
    return {node.name for node in tree(name).body if isinstance(node, ast.ClassDef)}


def package_imports(name):
    """The hardware_monitor modules a module imports (relative or absolute)."""
    found = set()
    for node in ast.walk(tree(name)):
        if isinstance(node, ast.ImportFrom):
            if node.level:  # from .x import y / from . import x
                found.update([node.module] if node.module else [alias.name for alias in node.names])
            elif node.module and node.module.startswith("hardware_monitor"):
                found.add(node.module.removeprefix("hardware_monitor").lstrip(".") or "__init__")
    return found


def record(component_type, **fields):
    return {"component_type": component_type, "component_id": "X-1", "workstation_id": "WS-1", **fields}


# --- Strategy: hardware_monitor/assignment.py ---

def test_the_strategies_have_their_own_module():
    assert classes_in("assignment") == STRATEGIES


def test_every_class_there_is_a_strategy():
    assignment = module("assignment")
    for name in STRATEGIES - {"AssignmentStrategy"}:
        assert issubclass(getattr(assignment, name), assignment.AssignmentStrategy)


def test_the_strategy_module_depends_on_nothing_else_in_the_package():
    assert package_imports("assignment") == set()


def test_the_desk_module_defines_no_strategy_of_its_own():
    assert not {name for name in classes_in("maintenance") if name.endswith("Strategy")}


@pytest.mark.parametrize("name", sorted(STRATEGIES))
def test_the_old_import_path_still_gives_the_same_strategy(name):
    assert getattr(maintenance, name) is getattr(module("assignment"), name)


def test_the_desk_defaults_to_the_strategy_module_s_fewest_calls(technicians, tmp_path):
    desk = maintenance.MaintenanceDesk(technicians, log_path=str(tmp_path / "log.jsonl"), report_csv_path=None)
    assert type(desk.assignment_strategy) is module("assignment").FewestCallsStrategy


# --- Factory: hardware_monitor/factory.py ---

@pytest.mark.parametrize("component_type, product", [
    ("Camera", Camera),
    ("camera", Camera),
    ("  MICROPHONE ", Microphone),
], ids=["capitalised", "lower-case", "spaces-and-upper-case"])
def test_create_component_picks_the_product_from_the_record_type(component_type, product):
    component = module("factory").create_component(record(component_type))
    assert type(component) is product and component.component_id == "X-1"


def test_create_component_builds_what_the_subclass_factory_builds():
    data = record("Camera", camera_index=2, resolution="1280x720", connection_status="INACTIVE")
    built, direct = module("factory").create_component(data), Camera.from_dict(data)
    assert vars(built) == vars(direct)


def test_the_factory_knows_the_project_s_component_types():
    assert {"camera", "microphone"} <= set(module("factory").component_types())


def test_an_unknown_type_is_rejected_with_the_known_ones_listed():
    with pytest.raises(ValueError, match="Unknown component_type 'Speaker'") as error:
        module("factory").create_component(record("Speaker"))
    assert "camera" in str(error.value) and "microphone" in str(error.value)


def test_a_record_without_a_type_is_rejected():
    data = record("Camera")
    del data["component_type"]
    with pytest.raises(ValueError, match="component_type"):
        module("factory").create_component(data)


def test_the_factory_module_depends_only_on_the_models():
    assert package_imports("factory") == {"models"}
    assert classes_in("factory") == set()  # it builds the models' classes, it defines none


def test_the_base_class_from_dict_hands_the_record_to_the_factory():
    factory = module("factory")
    data = record("Microphone")
    with mock.patch.object(factory, "create_component", wraps=factory.create_component) as create:
        component = HardwareComponent.from_dict(data)

    create.assert_called_once_with(data)
    assert type(component) is Microphone


def test_the_models_no_longer_pick_the_subclass_themselves():
    source = ast.unparse(tree("models"))
    assert "__subclasses__" not in source
