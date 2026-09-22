"""Loads synthetic workstation/component records from data/sample_data.jsonl."""

import json

from .models import Camera, Microphone, Workstation

COMPONENT_CLASSES = {
    "camera": Camera,
    "microphone": Microphone,
}

_KNOWN_WORKSTATION_FIELDS = {"workstation_id", "workstation_name", "location"}
_KNOWN_COMPONENT_FIELDS = {"component_type", "component_id", "name", "connection_status"}


def load_workstations_from_jsonl(path):
    """Reads the JSONL file strictly line-by-line (no .read()/.readlines()),
    parses each record with json.loads, and instantiates the matching
    Workstation/HardwareComponent objects."""
    workstations = {}

    with open(path, "r", encoding="utf-8") as data_file:
        for line in data_file:
            line = line.strip()
            if not line:
                continue

            record = json.loads(line)
            ws_id = record["workstation_id"]

            if ws_id not in workstations:
                workstations[ws_id] = Workstation(
                    workstation_id=ws_id,
                    name=record.get("workstation_name", ws_id),
                    location=record.get("location", ""),
                )

            component_cls = COMPONENT_CLASSES.get(record["component_type"].lower())
            if component_cls is None:
                continue

            extra_fields = {
                key: value for key, value in record.items()
                if key not in _KNOWN_WORKSTATION_FIELDS and key not in _KNOWN_COMPONENT_FIELDS
            }

            component = component_cls(
                component_id=record["component_id"],
                name=record.get("name", record["component_id"]),
                workstation_id=ws_id,
                connection_status=record.get("connection_status", "ACTIVE"),
                **extra_fields,
            )
            workstations[ws_id].add_component(component)

    return list(workstations.values())
