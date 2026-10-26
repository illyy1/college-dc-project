"""Live alert statistics for the statistics viewer: how many maintenance
calls there are of each alert type, how the calls are spread across the
technicians, and who took each recent call. Everything is computed from the
maintenance log, so the numbers cover every call ever made, including ones
from earlier runs and from dashboards running in another process."""

from dataclasses import dataclass
from datetime import datetime

from .repository import load_maintenance_history, load_technicians_from_jsonl, request_number

UNASSIGNED = "Unassigned"
# error_type prefix -> alert type; anything else is "Other"
ALERT_TYPE_FOR_PREFIX = {"CAMERA": "Camera", "MICROPHONE": "Microphone"}


@dataclass
class AlertTypeCount:
    alert_type: str
    total: int
    open: int
    resolved: int


@dataclass
class TechnicianLoad:
    tech_id: str | None  # None for the calls nobody was assigned to
    name: str
    total: int
    open: int
    resolved: int
    share: float  # percent of all calls


@dataclass
class CallRow:
    request_id: str
    alert_type: str
    error_type: str
    workstation_id: str
    technician: str
    status: str
    opened_at: datetime | None


@dataclass
class Stats:
    total_calls: int
    open_calls: int
    resolved_calls: int
    alert_types: list
    technicians: list
    recent_calls: list


def alert_type_for(error_type):
    """CAMERA_FAILURE -> "Camera", MICROPHONE_SILENCE -> "Microphone", else "Other"."""
    prefix = (error_type or "").split("_", 1)[0].upper()
    return ALERT_TYPE_FOR_PREFIX.get(prefix, "Other")


def is_open(call):
    return call.status != "RESOLVED"


def technician_name(call, technicians):
    """Who took the call: the technician's current name from the data file
    (not the one saved in the log), the logged name or id for a technician
    who has since been removed, or "Unassigned"."""
    tech_id = call.assigned_tech_id
    if not tech_id:
        return UNASSIGNED
    if tech_id in technicians:
        return technicians[tech_id].name
    return call.assigned_tech_name or tech_id


def count_alert_types(history):
    """Calls per alert type, busiest first; equal counts alphabetically."""
    types = {}
    for call in history:
        alert_type = alert_type_for(call.error_type)
        count = types.setdefault(alert_type, AlertTypeCount(alert_type, 0, 0, 0))
        count.total += 1
        count.open += is_open(call)
    for count in types.values():
        count.resolved = count.total - count.open
    return sorted(types.values(), key=lambda t: (-t.total, t.alert_type))


def technician_loads(history, technicians):
    """Each technician's calls and share of all calls, busiest first; equal
    counts alphabetically. Every technician is listed, even with no calls,
    so the spread shows who is idle."""
    loads = {tech_id: TechnicianLoad(tech_id, tech.name, 0, 0, 0, 0.0) for tech_id, tech in technicians.items()}
    for call in history:
        tech_id = call.assigned_tech_id or None
        load = loads.setdefault(tech_id, TechnicianLoad(tech_id, technician_name(call, technicians), 0, 0, 0, 0.0))
        load.total += 1
        load.open += is_open(call)
    for load in loads.values():
        load.resolved = load.total - load.open
        load.share = 100 * load.total / len(history) if history else 0.0
    return sorted(loads.values(), key=lambda t: (-t.total, t.name))


def recent_calls(history, technicians, limit):
    """The newest calls first, with who took each. Calls opened at the same
    moment: highest number first; calls with no open time go last."""
    newest_first = sorted(
        history,
        key=lambda c: (c.opened_at is not None, c.opened_at or datetime.min, request_number(c.request_id)),
        reverse=True,
    )
    return [
        CallRow(call.request_id, alert_type_for(call.error_type), call.error_type, call.workstation_id,
                technician_name(call, technicians), call.status, call.opened_at)
        for call in newest_first[:limit]
    ]


def compute_stats(history, technicians, recent_limit=10):
    """history: the latest state of each call, as MaintenanceRequest objects
    (load_maintenance_history); technicians: tech_id -> Technician."""
    open_calls = sum(1 for call in history if is_open(call))
    return Stats(
        total_calls=len(history),
        open_calls=open_calls,
        resolved_calls=len(history) - open_calls,
        alert_types=count_alert_types(history),
        technicians=technician_loads(history, technicians),
        recent_calls=recent_calls(history, technicians, recent_limit),
    )


def load_stats(log_path, technicians_path, recent_limit=10, reported=None):
    """Reads the maintenance log and the technician file and computes the
    statistics. A log that doesn't exist yet means no calls. reported: see
    repository.iter_jsonl_records."""
    return compute_stats(load_maintenance_history(log_path, reported),
                         load_technicians_from_jsonl(technicians_path), recent_limit)
