"""Orchestrator only — no class definitions live here.

Loads the synthetic workstation fleet from data/sample_data.jsonl, runs a
linear demonstration of the OOP model, data structures/queues, iterators,
lazy pipeline, context manager and maintenance workflow working together,
then launches the live hardware-monitoring dashboard for this machine. When
the dashboard is closed, the maintenance history report is printed and
exported to CSV.

    python main.py            demo + live dashboard + history report
    python main.py --report   only print/export the maintenance history report
"""

import argparse

from hardware_monitor.config import (
    SAMPLE_DATA_PATH,
    TECHNICIANS_PATH,
    MAINTENANCE_LOG_PATH,
    MAINTENANCE_REPORT_CSV_PATH,
)
from hardware_monitor.repository import (
    load_workstations_from_jsonl,
    load_technicians_from_jsonl,
)
from hardware_monitor.processing import Alert, AlertManager
from hardware_monitor.iterators import (
    AlertHistory,
    iter_disconnected_components,
    lazy_disconnected_pipeline,
)
from hardware_monitor.context_managers import HardwareDiagnosticSession
from hardware_monitor.models import MaintenanceRequest
from hardware_monitor.maintenance import MaintenanceDesk
from hardware_monitor.reports import (
    validate_and_assign_request,
    generate_technician_workload_report,
    generate_maintenance_history_report,
    export_maintenance_history_csv,
)
from hardware_monitor.dashboard import HardwareMonitorApp


def run_diagnostics_and_log_alerts(workstations, alert_manager):
    """Polymorphically tests every component inside a diagnostic session and
    logs an Alert for anything unhealthy."""
    for workstation in workstations:
        for component in workstation.components:
            with HardwareDiagnosticSession(component) as tested:
                healthy = tested.run_diagnostics()

            if healthy:
                continue

            priority = 1 if tested.component_type == "Camera" else 4
            alert = Alert(
                component_id=tested.component_id,
                workstation_id=workstation.workstation_id,
                error_type=f"{tested.component_type.upper()}_FAILURE",
                message=f"{tested.component_type} '{tested.name}' failed diagnostics.",
                priority=priority,
            )
            alert_manager.log_alert(alert)


def demonstrate_information_system():
    print("=" * 70)
    print("STEP 1 — Loading synthetic workstation fleet from JSONL")
    workstations = load_workstations_from_jsonl(SAMPLE_DATA_PATH)
    for workstation in workstations:
        print(f"  {workstation}")

    print("\nSTEP 2 — Polymorphic diagnostics inside HardwareDiagnosticSession")
    alert_manager = AlertManager()
    run_diagnostics_and_log_alerts(workstations, alert_manager)
    print(f"  Logged {len(alert_manager.alert_history)} alert(s) from failed diagnostics.")

    print("\nSTEP 3 — Data-structure operations")
    print("  Unique error types (set):", alert_manager.unique_error_types())
    print("  Alerts grouped by workstation (dict):")
    for ws_id, alerts in alert_manager.group_by_workstation().items():
        print(f"    {ws_id}: {len(alerts)} alert(s)")
    print("  Alerts sorted by priority (sorted + lambda key):")
    for alert in alert_manager.sorted_by_priority():
        print(f"    {alert}")

    print("\nSTEP 4 — Draining the queues")
    print(f"  Next critical alert (heapq):     {alert_manager.process_next_critical()}")
    print(f"  Next routine alert (deque FIFO): {alert_manager.process_next_maintenance()}")

    print("\nSTEP 5 — Custom Iterable/Iterator over unresolved alerts")
    for alert in AlertHistory(alert_manager.alert_history):
        print(f"  Unresolved: {alert}")

    print("\nSTEP 6 — Generator yielding disconnected components")
    for component in iter_disconnected_components(workstations):
        print(f"  Disconnected: {component}")

    print("\nSTEP 7 — 3-step lazy evaluation pipeline (stopping after 3 results)")
    pipeline = lazy_disconnected_pipeline(workstations)
    for index, component_id in enumerate(pipeline):
        if index >= 3:
            break
        print(f"  Lazy pipeline id: {component_id}")

    print("\nSTEP 8 — Maintenance calls opened and assigned to technicians")
    maintenance_desk = demonstrate_maintenance_workflow(alert_manager)

    print("=" * 70)
    return maintenance_desk


def demonstrate_maintenance_workflow(alert_manager):
    """Loads the technicians, opens a maintenance call for every unresolved
    alert (auto-assigned by specialty and workload, and saved to the
    maintenance log), resolves one, and prints the workload report."""
    technicians = load_technicians_from_jsonl(TECHNICIANS_PATH)
    maintenance_desk = MaintenanceDesk(technicians)

    requests = [
        maintenance_desk.open_request_for_alert(alert)
        for alert in AlertHistory(alert_manager.alert_history)
    ]

    if requests:
        resolved = maintenance_desk.resolve_request(requests[0].request_id)
        print(f"  Resolved: {resolved!r} by {maintenance_desk.technician_for(resolved).name}")

    try:
        validate_and_assign_request(MaintenanceRequest("MR-X", 0, 5), "T99", technicians)
    except ValueError as error:
        print(f"  Validation rejected unknown technician: {error}")

    for tech in technicians.values():
        print(f"  {tech}")

    generate_technician_workload_report(technicians)
    return maintenance_desk


def show_maintenance_reports():
    generate_maintenance_history_report(MAINTENANCE_LOG_PATH)
    export_maintenance_history_csv(MAINTENANCE_LOG_PATH, MAINTENANCE_REPORT_CSV_PATH)


def main():
    parser = argparse.ArgumentParser(description="Room hardware monitor")
    parser.add_argument("--report", action="store_true",
                        help="only print and export the maintenance history report")
    args = parser.parse_args()

    if args.report:
        show_maintenance_reports()
        return

    maintenance_desk = demonstrate_information_system()

    print("\nLaunching live hardware monitoring dashboard for this workstation...")
    app = HardwareMonitorApp(maintenance_desk)
    try:
        app.start()
    finally:
        # also runs on Ctrl+C; the CSV itself is already refreshed on every call change
        show_maintenance_reports()


if __name__ == "__main__":
    main()
