"""Loads synthetic workstation/component records from data/sample_data.jsonl,
technicians from data/technicians.jsonl, and the maintenance request history
from logs/maintenance_requests.jsonl."""

import json
import os

from .models import Camera, Microphone, Workstation, Technician

COMPONENT_CLASSES = {
    "camera": Camera,
    "microphone": Microphone,
}

_KNOWN_WORKSTATION_FIELDS = {"workstation_id", "workstation_name", "location"}
_KNOWN_COMPONENT_FIELDS = {"component_type", "component_id", "name", "connection_status"}
_REQUIRED_TECHNICIAN_FIELDS = ("tech_id", "name", "specialty")


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


def load_technicians_from_jsonl(path):
    """Reads technician records line-by-line and returns a dict of
    tech_id -> Technician. Raises ValueError (with the line number) for a
    record missing a required field or reusing an existing tech_id, since
    maintenance calls reference technicians by that id."""
    technicians = {}

    with open(path, "r", encoding="utf-8") as data_file:
        for line_number, line in enumerate(data_file, start=1):
            line = line.strip()
            if not line:
                continue

            record = json.loads(line)
            missing = [f for f in _REQUIRED_TECHNICIAN_FIELDS if not str(record.get(f, "")).strip()]
            if missing:
                raise ValueError(f"{path} line {line_number}: technician is missing {', '.join(missing)}.")
            if record["tech_id"] in technicians:
                raise ValueError(f"{path} line {line_number}: duplicate tech_id '{record['tech_id']}'.")

            technicians[record["tech_id"]] = Technician(
                tech_id=record["tech_id"],
                name=record["name"],
                specialty=record["specialty"],
            )

    return technicians


def load_maintenance_history(path):
    """Reads the append-only maintenance log line-by-line. Every state change
    of a request is its own line, so the last line seen per request_id is its
    current state. Returns those latest records ordered by request_id."""
    if not os.path.exists(path):
        return []

    latest = {}
    for record in iter_jsonl_records(path):
        if "request_id" in record:
            latest[record["request_id"]] = record

    return sorted(latest.values(), key=lambda r: request_number(r["request_id"]))


def request_number(request_id):
    """'MR-0012' -> 12. Unknown formats sort first (0) instead of crashing."""
    try:
        return int(str(request_id).rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def append_jsonl_record(path, record):
    """Appends one JSON record as its own line. A run killed mid-write can
    leave a last line without "\\n"; this starts on a fresh line so the new
    record isn't glued onto (and lost with) that fragment."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    needs_newline = False
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as existing:
            existing.seek(-1, os.SEEK_END)
            needs_newline = existing.read(1) != b"\n"
    with open(path, "a", encoding="utf-8") as log_file:
        if needs_newline:
            log_file.write("\n")
        log_file.write(json.dumps(record, ensure_ascii=False) + "\n")


def iter_jsonl_records(path):
    """Yields each JSON record of an append-only log line-by-line, skipping
    (with a warning) lines that are not valid JSON — e.g. a half-written last
    line left behind when the program was killed mid-write."""
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as log_file:
        for line_number, line in enumerate(log_file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                print(f"Warning: skipping corrupt line {line_number} in {path}.")
                continue
            if isinstance(record, dict):
                yield record
