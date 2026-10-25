"""Business reports: validated assignment of maintenance requests to
technicians, a per-technician workload report, and the full maintenance
history report (who handled which error) with a CSV export."""

import csv
import os

from .config import MAINTENANCE_LOG_PATH, MAINTENANCE_REPORT_CSV_PATH

from .models import MaintenanceRequest, Technician
from .repository import load_maintenance_history

HISTORY_CSV_FIELDS = [
    "request_id", "status", "priority", "error_type", "workstation_id",
    "component_id", "assigned_tech_id", "assigned_tech_name",
    "opened_at", "resolved_at", "alert_id",
]


def validate_and_assign_request(request: MaintenanceRequest, tech_id: str, technicians_dict: dict[str, Technician]):
    if tech_id not in technicians_dict:
        raise ValueError(f"שגיאת אימות נתונים: טכנאי שמזהה '{tech_id}' אינו קיים במערכת!")
    if request.status == "RESOLVED":
        raise ValueError(f"שגיאת אימות נתונים: בקשה {request.request_id} כבר טופלה ולא ניתן לשייך אותה מחדש!")

    # אם הבקשה כבר שויכה לטכנאי אחר - מסירים אותה מהרשימה שלו
    previous_tech = technicians_dict.get(request.assigned_tech_id)
    if previous_tech is not None and request in previous_tech.assigned_requests:
        previous_tech.assigned_requests.remove(request)

    request.assign_to(tech_id)
    technicians_dict[tech_id].assigned_requests.append(request)
    print(f"[SUCCESS] בקשה {request.request_id} שויכה בהצלחה לטכנאי {technicians_dict[tech_id].name}")


def find_unknown_technician_references(history, technicians_dict: dict[str, Technician]):
    """Cross-file validation: maintenance-log records whose assigned_tech_id
    does not exist in the technician data file."""
    return [
        record for record in history
        if record.get("assigned_tech_id") and record["assigned_tech_id"] not in technicians_dict
    ]


def generate_technician_workload_report(technicians_dict: dict[str, Technician]):
    print("\n" + "=" * 40)
    print("      דוח עסקי: עומס עבודה לפי טכנאי")
    print("=" * 40)

    for tech_id, tech in technicians_dict.items():
        open_tasks = [r for r in tech.assigned_requests if r.status != "RESOLVED"]
        print(f"• {tech.name} ({tech_id})")
        print(f"  משימות פעילות לטיפול: {len(open_tasks)}")
        for task in open_tasks:
            print(f"    - {task}")
    print("=" * 40 + "\n")


def generate_maintenance_history_report(log_path):
    """Prints every call ever opened (open and resolved) with the technician
    that handled it, read from the persisted maintenance log."""
    history = load_maintenance_history(log_path)

    print("\n" + "=" * 60)
    print("      דוח היסטוריה: קריאות שירות וטכנאי מטפל")
    print("=" * 60)

    if not history:
        print("  אין קריאות שירות בהיסטוריה.")

    for record in history:
        tech = record.get("assigned_tech_name") or record.get("assigned_tech_id") or "לא שויך"
        print(f"• {record['request_id']} | {record['error_type']} | עמדה: {record['workstation_id']}"
              f" | רכיב: {record['component_id']}")
        print(f"    טכנאי מטפל: {tech} | סטטוס: {record['status']} | עדיפות: {record['priority']}")
        print(f"    נפתחה: {record['opened_at']} | נסגרה: {record.get('resolved_at') or '—'}")

    by_tech = {}
    for record in history:
        tech = record.get("assigned_tech_name") or "לא שויך"
        by_tech[tech] = by_tech.get(tech, 0) + 1
    if by_tech:
        print("-" * 60)
        print("  סה\"כ קריאות לפי טכנאי:")
        for tech, total in sorted(by_tech.items(), key=lambda item: item[1], reverse=True):
            print(f"    {tech}: {total}")
    print("=" * 60 + "\n")
    return history


def export_maintenance_history_csv(log_path, csv_path, verbose=True):
    """Writes the maintenance history to a CSV file (opens in Excel)."""
    history = load_maintenance_history(log_path)

    os.makedirs(os.path.dirname(csv_path), exist_ok=True)  # e.g. `--report` after the logs folder was deleted
    # utf-8-sig so Excel displays the Hebrew/technician names correctly
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=HISTORY_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(history)

    if verbose:
        print(f"[REPORT] Maintenance history exported to {csv_path} ({len(history)} call(s))")
    return csv_path


def publish_maintenance_reports(log_path=MAINTENANCE_LOG_PATH, csv_path=MAINTENANCE_REPORT_CSV_PATH):
    """Prints the maintenance history report and exports it to CSV."""
    generate_maintenance_history_report(log_path)
    export_maintenance_history_csv(log_path, csv_path)
