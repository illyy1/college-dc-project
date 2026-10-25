"""Live alert statistics for the statistics viewer: how many maintenance
calls there are of each alert type, how the calls are spread across the
technicians, and who took each recent call. Everything is computed from the
maintenance log, so the numbers cover every call ever made, including ones
from earlier runs and from dashboards running in another process."""

from dataclasses import dataclass

from .maintenance import specialty_for_error_type
from .repository import load_maintenance_history, load_technicians_from_jsonl, request_number

UNASSIGNED = "Unassigned"
# alert types follow the technician specialties; General work shows as "Other"
ALERT_TYPE_FOR_SPECIALTY = {"Camera": "Camera", "Microphone": "Microphone", "General": "Other"}


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
    opened_at: str | None


@dataclass
class Stats:
    total_calls: int
    open_calls: int
    resolved_calls: int
    alert_types: list
    technicians: list
    recent_calls: list


def alert_type_for(error_type):
    return ALERT_TYPE_FOR_SPECIALTY[specialty_for_error_type(error_type or "")]


def is_open(record):
    return record.get("status") != "RESOLVED"


def compute_stats(history, technicians, recent_limit=10):
    """history: the latest record of each call (load_maintenance_history);
    technicians: tech_id -> Technician."""
    total = len(history)
    open_calls = sum(1 for record in history if is_open(record))

    def technician_name(record):
        tech_id = record.get("assigned_tech_id")
        if not tech_id:
            return UNASSIGNED
        if tech_id in technicians:
            return technicians[tech_id].name  # the current name, not the one saved in the log
        return record.get("assigned_tech_name") or tech_id

    types = {}
    for record in history:
        count = types.setdefault(alert_type_for(record.get("error_type")), AlertTypeCount("", 0, 0, 0))
        count.total += 1
        count.open += is_open(record)
    for alert_type, count in types.items():
        count.alert_type, count.resolved = alert_type, count.total - count.open

    # every technician is listed, even with no calls, so the spread shows who is idle
    loads = {tech_id: TechnicianLoad(tech_id, tech.name, 0, 0, 0, 0.0) for tech_id, tech in technicians.items()}
    for record in history:
        tech_id = record.get("assigned_tech_id") or None
        load = loads.setdefault(tech_id, TechnicianLoad(tech_id, technician_name(record), 0, 0, 0, 0.0))
        load.total += 1
        load.open += is_open(record)
    for load in loads.values():
        load.resolved = load.total - load.open
        load.share = 100 * load.total / total if total else 0.0

    newest_first = sorted(
        history,
        key=lambda r: (r.get("opened_at") is not None, r.get("opened_at") or "", request_number(r.get("request_id"))),
        reverse=True,  # dated calls first, newest first; same moment: highest number first
    )
    recent = [
        CallRow(record.get("request_id"), alert_type_for(record.get("error_type")), record.get("error_type") or "",
                record.get("workstation_id") or "", technician_name(record), record.get("status") or "",
                record.get("opened_at"))
        for record in newest_first[:recent_limit]
    ]

    return Stats(
        total_calls=total,
        open_calls=open_calls,
        resolved_calls=total - open_calls,
        alert_types=sorted(types.values(), key=lambda t: (-t.total, t.alert_type)),
        technicians=sorted(loads.values(), key=lambda t: (-t.total, t.name)),
        recent_calls=recent,
    )


def load_stats(log_path, technicians_path, recent_limit=10, reported=None):
    """Reads the maintenance log and the technician file and computes the
    statistics. A log that doesn't exist yet means no calls. reported: see
    repository.iter_jsonl_records."""
    return compute_stats(load_maintenance_history(log_path, reported),
                         load_technicians_from_jsonl(technicians_path), recent_limit)
