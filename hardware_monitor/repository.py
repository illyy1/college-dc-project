"""Loads synthetic workstation/component records from data/sample_data.jsonl,
technicians from data/technicians.jsonl, and the maintenance request history
from logs/maintenance_requests.jsonl."""

import json
import os
from datetime import datetime

from .models import HardwareComponent, Workstation, Technician
from .retry import retry

# a log or report another program holds for a moment (antivirus, OneDrive, the
# search indexer; on Windows that is a PermissionError) is tried again shortly
FILE_WRITE_ATTEMPTS = 3
FILE_RETRY_BACKOFF_SECONDS = 0.1  # waits 0.1 s, then 0.2 s
TEMPORARY_FILE_ERRORS = (PermissionError,)


class DataFileError(ValueError):
    """A line of a data file that can't be loaded. The message names the file
    and line; main.py reports it instead of a traceback. A ValueError, so code
    that catches those still catches it."""


def _parse_record(path, line_number, line):
    """json.loads one line into a dict, or raises DataFileError naming the line."""
    try:
        record = json.loads(line)
    except json.JSONDecodeError as error:
        raise DataFileError(f"{path} line {line_number}: invalid JSON ({error.msg}).") from None
    if not isinstance(record, dict):
        raise DataFileError(f"{path} line {line_number}: expected a JSON object.")
    return record


def load_workstations_from_jsonl(path):
    """Reads the JSONL file strictly line-by-line (no .read()/.readlines()),
    parses each record with json.loads, validates it and instantiates the
    objects through the Workstation.from_dict / HardwareComponent.from_dict
    factories. Raises DataFileError (with the line number) for a malformed
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
                raise DataFileError(f"{path} line {line_number}: {error}") from None

            existing = workstations.setdefault(workstation.workstation_id, workstation)
            if (existing.name, existing.location) != (workstation.name, workstation.location):
                raise DataFileError(
                    f"{path} line {line_number}: workstation '{workstation.workstation_id}' "
                    f"has a different name/location than on an earlier line."
                )
            if component.component_id in seen_component_ids:
                raise DataFileError(
                    f"{path} line {line_number}: duplicate component_id '{component.component_id}'."
                )
            seen_component_ids.add(component.component_id)
            existing.add_component(component)

    return list(workstations.values())


def load_technicians_from_jsonl(path):
    """Reads technician records line-by-line and returns a dict of
    tech_id -> Technician built by Technician.from_dict. Raises DataFileError
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
                raise DataFileError(f"{path} line {line_number}: {error}") from None
            if technician.tech_id in technicians:
                raise DataFileError(f"{path} line {line_number}: duplicate tech_id '{technician.tech_id}'.")

            technicians[technician.tech_id] = technician

    return technicians


def load_maintenance_history(path, reported=None):
    """Reads the append-only maintenance log line-by-line. Every state change
    of a request is its own line, so the last line seen per request_id is its
    current state. Returns those latest records ordered by request_id.

    A line with a wrongly typed field (e.g. from a hand-edited log) is
    skipped with a warning, so the call keeps its last readable state, or
    is left out if it has none. reported: see iter_jsonl_records."""
    if not os.path.exists(path):
        return []

    latest = {}
    for line_number, record in _numbered_records(path, reported):
        if "request_id" not in record:
            continue
        field = _wrongly_typed_field(record)
        if field is not None:
            _warn_once(reported, path, line_number,
                       f"Warning: skipping unreadable call on line {line_number} in {path} "
                       f"({field} is {record[field]!r}).")
            continue
        latest[record["request_id"]] = record

    return sorted(latest.values(), key=lambda r: request_number(r["request_id"]))


def highest_request_number(path, reported=None):
    """The highest request number anywhere in the log, unreadable calls
    included, so a new call never reuses a number that is already taken.
    0 for an empty or missing log. reported: see iter_jsonl_records."""
    return max((request_number(record["request_id"]) for record in iter_jsonl_records(path, reported)
                if "request_id" in record), default=0)


# fields the program reads as text, and must be text (or missing) in the log
_TEXT_FIELDS = ("request_id", "assigned_tech_id", "error_type", "opened_at")


def _wrongly_typed_field(record):
    """The first field of a log record with the wrong type, or None."""
    for field in _TEXT_FIELDS:
        value = record.get(field)
        if value is not None and not isinstance(value, str):
            return field
    if record.get("opened_at"):
        try:
            datetime.fromisoformat(record["opened_at"])
        except ValueError:
            return "opened_at"
    return None


def request_number(request_id):
    """'MR-0012' -> 12. Unknown formats sort first (0) instead of crashing."""
    try:
        return int(str(request_id).rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def append_jsonl_record(path, record):
    """Appends one JSON record as its own line. A run killed mid-write can
    leave a last line without "\\n"; this starts on a fresh line so the new
    record isn't glued onto (and lost with) that fragment.

    A file held for a moment by another program is tried again; if it is
    still held after the last attempt, or the write fails for another reason
    (a full disk), the OSError is raised."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    retry(lambda: _append_line(path, record), retry_on=TEMPORARY_FILE_ERRORS, max_attempts=FILE_WRITE_ATTEMPTS,
          backoff_seconds=FILE_RETRY_BACKOFF_SECONDS, label=f"Writing {path}")


def delete_log(path):
    """Deletes a log, if there is one, to start it afresh. A file held for a
    moment by another program is tried again, as when appending."""
    def remove():
        try:
            os.remove(path)
        except FileNotFoundError:
            pass  # nothing to reset

    retry(remove, retry_on=TEMPORARY_FILE_ERRORS, max_attempts=FILE_WRITE_ATTEMPTS,
          backoff_seconds=FILE_RETRY_BACKOFF_SECONDS, label=f"Deleting {path}")


def _append_line(path, record):
    """One attempt of append_jsonl_record. Checks the last byte each time, so a
    retry after a partly written attempt still starts on a fresh line."""
    needs_newline = False
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as existing:
            existing.seek(-1, os.SEEK_END)
            needs_newline = existing.read(1) != b"\n"
    with open(path, "a", encoding="utf-8") as log_file:
        if needs_newline:
            log_file.write("\n")
        log_file.write(json.dumps(record, ensure_ascii=False) + "\n")


def iter_jsonl_records(path, reported=None):
    """Yields each JSON record of an append-only log line-by-line, skipping
    (with a warning) lines that are not valid JSON — e.g. a half-written last
    line left behind when the program was killed mid-write.

    reported: for a caller that re-reads the same log again and again (the
    statistics viewer), a set that remembers which broken lines were already
    reported, so each is reported once instead of on every read."""
    for _, record in _numbered_records(path, reported):
        yield record


def _numbered_records(path, reported):
    """iter_jsonl_records, with each record's line number."""
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
                _warn_once(reported, path, line_number, f"Warning: skipping corrupt line {line_number} in {path}.")
                continue
            if isinstance(record, dict):
                yield line_number, record


def _warn_once(reported, path, line_number, message):
    """Prints the warning for this line, unless `reported` says it already was."""
    if reported is None or (path, line_number) not in reported:
        print(message)
        if reported is not None:
            reported.add((path, line_number))
