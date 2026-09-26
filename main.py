"""Orchestrator only — no class definitions live here.

Loads the synthetic workstation fleet from data/sample_data.jsonl, runs a
linear demonstration of the OOP model, data structures/queues, iterators,
lazy pipeline, context manager and maintenance workflow working together,
then launches the live hardware-monitoring dashboard for this machine. When
the dashboard is closed, the maintenance history report is printed and
exported to CSV.

    python main.py                  demo + live dashboard + history report
    python main.py --no-dashboard   demo + history report, no window (runs start to finish)
    python main.py --report         only print/export the maintenance history report
"""

import argparse
import os

from hardware_monitor.config import (
    SAMPLE_DATA_PATH,
    TECHNICIANS_PATH,
    MAINTENANCE_LOG_PATH,
    MAINTENANCE_REPORT_CSV_PATH,
    DEMO_MAINTENANCE_LOG_PATH,
)
from hardware_monitor.repository import (
    load_workstations_from_jsonl,
    load_technicians_from_jsonl,
    load_maintenance_history,
)
from hardware_monitor.processing import Alert, AlertManager
from hardware_monitor.iterators import (
    AlertHistory,
    iter_disconnected_components,
    lazy_disconnected_pipeline,
)
from hardware_monitor.context_managers import DiagnosticConnection
from hardware_monitor.models import Camera, MaintenanceRequest
from hardware_monitor.maintenance import MaintenanceDesk
from hardware_monitor.reports import (
    validate_and_assign_request,
    find_unknown_technician_references,
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
            try:
                with DiagnosticConnection(component) as tested:
                    healthy = tested.run_diagnostics()
            except Exception:
                healthy = False  # DiagnosticConnection already marked it ERROR
                tested = component

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

    print("\nSTEP 2 — Polymorphic diagnostics inside DiagnosticConnection")
    alert_manager = AlertManager()
    run_diagnostics_and_log_alerts(workstations, alert_manager)
    print(f"  Logged {len(alert_manager.alert_history)} alert(s) from failed diagnostics.")

    print("\nSTEP 3 — Encapsulation and safe diagnostic connections")
    sample = workstations[0].components[0]
    try:
        sample.connection_status = "MELTED"
    except ValueError as error:
        print(f"  Rejected illegal status for {sample.component_id}: {error}")

    spare = Camera("CAM-SPARE", "Spare Cam", "BENCH")
    connection = DiagnosticConnection(spare)
    try:
        with connection:
            print(f"  Testing {spare.component_id} on port {connection.port} (status {spare.connection_status})")
            raise RuntimeError("probe cable pulled mid-test")
    except RuntimeError:
        pass
    print(f"  After the crash: status {spare.connection_status}, "
          f"free ports {DiagnosticConnection.available_ports()}")

    print("\nSTEP 4 — Data-structure operations")
    print("  Unique error types (set):", alert_manager.unique_error_types())
    print("  Alerts grouped by workstation (dict):")
    for ws_id, alerts in alert_manager.group_by_workstation().items():
        print(f"    {ws_id}: {len(alerts)} alert(s)")
    print("  Alerts sorted by priority (sorted + lambda key):")
    sorted_alerts = alert_manager.sorted_by_priority()
    for alert in sorted_alerts:
        print(f"    {alert}")

    for alert in sorted_alerts[:2]:
        alert_manager.acknowledge_alert(alert.alert_id)
    if len(sorted_alerts) > 1:
        alert_manager.unacknowledge_alert(sorted_alerts[1].alert_id)
    print("  Acknowledged alert ids (set.add, then set.discard):", alert_manager.acknowledged_alert_ids)
    common = alert_manager.critical_and_routine_common_error_types()
    print("  Error types in both the critical and routine queues (set &):",
          common or "none — critical and routine faults are separate issues")

    print("\nSTEP 5 — Draining the queues")
    critical = alert_manager.process_next_critical()
    print(f"  Next critical alert (heapq):     {critical}")
    print(f"  Next routine alert (deque FIFO): {alert_manager.process_next_maintenance()}")
    if critical is not None:
        critical.resolved = True
        print(f"  Critical alert #{critical.alert_id} fixed on site and marked resolved.")

    print("\nSTEP 6 — Custom Iterable/Iterator over unresolved alerts")
    history = AlertHistory(alert_manager.alert_history)
    iterator = iter(history)
    print(f"  iter(AlertHistory) returned an {type(iterator).__name__}")
    shown = 0
    for alert in iterator:
        print(f"  Unresolved: {alert}")
        shown += 1
    print(f"  Skipped {len(history) - shown} resolved alert(s) transparently.")

    print("\nSTEP 7 — Generator yielding disconnected components")
    for component in iter_disconnected_components(workstations):
        print(f"  Disconnected: {component}")

    print("\nSTEP 8 — 3-step lazy evaluation pipeline (stopping after 3 results)")
    pipeline = lazy_disconnected_pipeline(workstations)
    for index, component_id in enumerate(pipeline):
        if index >= 3:
            break
        print(f"  Lazy pipeline id: {component_id}")

    print("\nSTEP 9 — Maintenance calls opened and assigned to technicians")
    demonstrate_maintenance_workflow(alert_manager)

    print("=" * 70)


def demonstrate_maintenance_workflow(alert_manager):
    """Loads the technicians, opens a maintenance call for every unresolved
    alert (auto-assigned by specialty and workload, and saved to the
    demo maintenance log), resolves one, and prints the workload report.

    The demo fleet is synthetic, so its calls go to a separate log that is
    reset every run and never reach the real maintenance history."""
    if os.path.exists(DEMO_MAINTENANCE_LOG_PATH):
        os.remove(DEMO_MAINTENANCE_LOG_PATH)
    technicians = load_technicians_from_jsonl(TECHNICIANS_PATH)
    maintenance_desk = MaintenanceDesk(technicians, log_path=DEMO_MAINTENANCE_LOG_PATH,
                                       report_csv_path=None)

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

    # cross-file validation: every technician named in the maintenance log must exist in technicians.jsonl
    history = load_maintenance_history(DEMO_MAINTENANCE_LOG_PATH)
    unknown = find_unknown_technician_references(history, technicians)
    print(f"  Cross-file check: {len(history)} call(s) in the log, {len(unknown)} with an unknown technician")
    tampered = history + [{"request_id": "MR-TAMPERED", "assigned_tech_id": "T42"}]
    for record in find_unknown_technician_references(tampered, technicians):
        print(f"  Cross-file check flagged {record['request_id']}: technician "
              f"'{record['assigned_tech_id']}' is not in technicians.jsonl")

    for tech in technicians.values():
        print(f"  {tech}")

    generate_technician_workload_report(technicians)


def show_maintenance_reports():
    generate_maintenance_history_report(MAINTENANCE_LOG_PATH)
    export_maintenance_history_csv(MAINTENANCE_LOG_PATH, MAINTENANCE_REPORT_CSV_PATH)


def main():
    parser = argparse.ArgumentParser(description="Room hardware monitor")
    parser.add_argument("--report", action="store_true",
                        help="only print and export the maintenance history report")
    parser.add_argument("--no-dashboard", action="store_true",
                        help="run the demo and reports without opening the live dashboard window")
    args = parser.parse_args()

    if args.report:
        show_maintenance_reports()
        return

    demonstrate_information_system()

    if args.no_dashboard:
        show_maintenance_reports()
        return

    print("\nLaunching live hardware monitoring dashboard for this workstation...")
    app = HardwareMonitorApp()  # uses the real maintenance log, not the demo one
    try:
        app.start()
    finally:
        # also runs on Ctrl+C; the CSV itself is already refreshed on every call change
        show_maintenance_reports()


if __name__ == "__main__":
    main()
