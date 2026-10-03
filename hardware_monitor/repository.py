"""Loads synthetic workstation/component records from data/sample_data.jsonl,
technicians from data/technicians.jsonl, and the maintenance request history
from logs/maintenance_requests.jsonl."""

import json
import os

from .models import HardwareComponent, Workstation, Technician


def _parse_record(path, line_number, line):
    """json.loads one line into a dict, or raises ValueError naming the line."""
    try:
        record = json.loads(line)
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} line {line_number}: invalid JSON ({error.msg}).") from None
    if not isinstance(record, dict):
        raise ValueError(f"{path} line {line_number}: expected a JSON object.")
    return record


def load_workstations_from_jsonl(path):
    """Reads the JSONL file strictly line-by-line (no .read()/.readlines()),
    parses each record with json.loads, validates it and instantiates the
    objects through the Workstation.from_dict / HardwareComponent.from_dict
    factories. Raises ValueError (with the line number) for a malformed
    record, a record whose workstation details contradict an earlier line,
    or a reused component_id."""
    workstations = {}
    seen_component_ids = set()

    with open(path, "r", encoding="utf-8") as data_file:
        for line_number, line in enumerate(data_file, start=1):
            line = line.strip()
            if not line:
                continue

            record = _parse_record(path, line_number, line)
            try:
                workstation = Workstation.from_dict(record)
                component = HardwareComponent.from_dict(record)
            except ValueError as error:
                raise ValueError(f"{path} line {line_number}: {error}") from None

            existing = workstations.setdefault(workstation.workstation_id, workstation)
            if (existing.name, existing.location) != (workstation.name, workstation.location):
                raise ValueError(
                    f"{path} line {line_number}: workstation '{workstation.workstation_id}' "
                    f"has a different name/location than on an earlier line."
                )
            if component.component_id in seen_component_ids:
                raise ValueError(
                    f"{path} line {line_number}: duplicate component_id '{component.component_id}'."
                )
            seen_component_ids.add(component.component_id)
            existing.add_component(component)

    return list(workstations.values())


def load_technicians_from_jsonl(path):
    """Reads technician records line-by-line and returns a dict of
    tech_id -> Technician built by Technician.from_dict. Raises ValueError
    (with the line number) for a record missing a required field or reusing
    an existing tech_id, since maintenance calls reference technicians by
    that id."""
    technicians = {}

    with open(path, "r", encoding="utf-8") as data_file:
        for line_number, line in enumerate(data_file, start=1):
            line = line.strip()
            if not line:
                continue

            record = _parse_record(path, line_number, line)
            try:
                technician = Technician.from_dict(record)
            except ValueError as error:
                raise ValueError(f"{path} line {line_number}: {error}") from None
            if technician.tech_id in technicians:
                raise ValueError(f"{path} line {line_number}: duplicate tech_id '{technician.tech_id}'.")

            technicians[technician.tech_id] = technician

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
