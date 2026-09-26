"""Linear demonstration of the information system: one function per step,
each showing one requirement (OOP model, encapsulation, context manager,
data structures, queues, iterators, generator, lazy pipeline, maintenance
workflow). run_demo() runs them in order without any interactive input."""

import os
from itertools import islice

from .config import SAMPLE_DATA_PATH, TECHNICIANS_PATH, DEMO_MAINTENANCE_LOG_PATH
from .context_managers import DiagnosticConnection
from .iterators import AlertHistory, iter_disconnected_components, lazy_disconnected_pipeline
from .maintenance import MaintenanceDesk
from .models import Camera, MaintenanceRequest
from .processing import AlertManager, run_diagnostics_and_log_alerts
from .reports import (
    validate_and_assign_request,
    find_unknown_technician_references,
    generate_technician_workload_report,
)
from .repository import load_workstations_from_jsonl, load_technicians_from_jsonl, load_maintenance_history


def run_demo():
    print("=" * 70)
    _header(1, "Loading synthetic workstation fleet from JSONL", first=True)
    workstations = demo_load_fleet()

    _header(2, "Polymorphic diagnostics inside DiagnosticConnection")
    alert_manager = demo_polymorphic_diagnostics(workstations)

    _header(3, "Encapsulation and safe diagnostic connections")
    demo_encapsulation_and_context_manager(workstations)

    _header(4, "Data-structure operations")
    demo_data_structures(alert_manager)

    _header(5, "Draining the queues")
    demo_queues(alert_manager)

    _header(6, "Custom Iterable/Iterator over unresolved alerts")
    demo_iterators(alert_manager)

    _header(7, "Generator yielding disconnected components")
    demo_generator(workstations)

    _header(8, "3-step lazy pipeline: disconnected -> older than 3 years -> id (first 3 only)")
    demo_lazy_pipeline(workstations, limit=3, min_age_years=3)

    _header(9, "Maintenance calls opened and assigned to technicians")
    demo_maintenance_workflow(alert_manager)
    print("=" * 70)


def _header(number, title, first=False):
    separator = "" if first else "\n"
    print(f"{separator}STEP {number} — {title}")


def demo_load_fleet():
    workstations = load_workstations_from_jsonl(SAMPLE_DATA_PATH)
    for workstation in workstations:
        print(f"  {workstation}")
    return workstations


def demo_polymorphic_diagnostics(workstations):
    alert_manager = AlertManager()
    run_diagnostics_and_log_alerts(workstations, alert_manager)
    print(f"  Logged {len(alert_manager.alert_history)} alert(s) from failed diagnostics.")
    return alert_manager


def demo_encapsulation_and_context_manager(workstations):
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


def demo_data_structures(alert_manager):
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


def demo_queues(alert_manager):
    critical = alert_manager.process_next_critical()
    print(f"  Next critical alert (heapq):     {critical}")
    print(f"  Next routine alert (deque FIFO): {alert_manager.process_next_maintenance()}")
    if critical is not None:
        critical.resolved = True
        print(f"  Critical alert #{critical.alert_id} fixed on site and marked resolved.")


def demo_iterators(alert_manager):
    history = AlertHistory(alert_manager.alert_history)
    iterator = iter(history)
    print(f"  iter(AlertHistory) returned an {type(iterator).__name__}")
    shown = 0
    for alert in iterator:
        print(f"  Unresolved: {alert}")
        shown += 1
    print(f"  Skipped {len(history) - shown} resolved alert(s) transparently.")

    # every iter() call gets its own ActiveAlertIterator, so two admins can
    # walk the same history at once without moving each other's position
    admin_a, admin_b = iter(history), iter(history)
    print("  Two IT admins walking the same history, taking turns:")
    for admin_name, admin_iterator in (("A", admin_a), ("A", admin_a), ("B", admin_b), ("A", admin_a), ("B", admin_b)):
        alert = next(admin_iterator)
        print(f"    Admin {admin_name} -> alert #{alert.alert_id}")
    remaining_for_b = list(admin_b)
    print(f"    Admin B finishes the rest: {[a.alert_id for a in remaining_for_b]}")
    try:
        next(admin_b)
    except StopIteration:
        print("    Admin B's iterator is exhausted: next() raised StopIteration")
    print(f"    Admin A is unaffected and still has {len(list(admin_a))} alert(s) left")


def demo_generator(workstations):
    for component in iter_disconnected_components(workstations):
        print(f"  Disconnected: {component}")


def demo_lazy_pipeline(workstations, limit, min_age_years):
    inspected = []

    def tracked(fleet):
        """Records each workstation the pipeline actually reaches."""
        for workstation in fleet:
            inspected.append(workstation.workstation_id)
            yield workstation

    pipeline = lazy_disconnected_pipeline(tracked(workstations), min_age_years=min_age_years)
    for component_id in islice(pipeline, limit):
        component = next(c for ws in workstations for c in ws.components if c.component_id == component_id)
        print(f"  Lazy pipeline id: {component_id} (installed {component.installed_on}, "
              f"{component.age_years:.1f} years old)")
    print(f"  Stopped after result #{limit}: inspected {len(inspected)} of {len(workstations)} workstations "
          f"(last: {inspected[-1]}); the rest were never touched.")


def demo_maintenance_workflow(alert_manager):
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
