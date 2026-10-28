"""Factory pattern: builds the right HardwareComponent from a data record.

The loaders read the workstations' components from JSONL, where each
record names its kind in "component_type". create_component() picks the
matching subclass (Camera, Microphone, ...) and has it build itself with its
own from_dict(), so no loader needs an if/elif chain over the kinds, and a
new subclass of HardwareComponent is picked up without changing anything
here. HardwareComponent.from_dict() hands records to this module."""

from .models import HardwareComponent, _require_fields


def _products():
    """{type name: subclass} for every kind of HardwareComponent."""
    return {product.__name__.lower(): product for product in HardwareComponent.__subclasses__()}


def component_types():
    """The component types the factory can build, sorted."""
    return sorted(_products())


def create_component(record):
    """The HardwareComponent subclass named by record["component_type"] (any
    case, spaces ignored), built from the record. Raises ValueError on a
    missing field, an unknown type or a bad value."""
    _require_fields(record, HardwareComponent.REQUIRED_FIELDS, "Component")
    products = _products()
    component_type = record["component_type"].strip().lower()
    if component_type not in products:
        raise ValueError(f"Unknown component_type '{record['component_type']}'. "
                         f"Must be one of {sorted(products)}.")
    return products[component_type].from_dict(record)
