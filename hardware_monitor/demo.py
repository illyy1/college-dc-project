"""Linear demonstration of the information system: one function per step,
each showing one requirement (OOP model, composition, encapsulation, context
manager, data structures, queues, iterators, generator, lazy pipeline,
maintenance workflow, async information gathering). run_demo() runs them in
order without any interactive input."""

import asyncio
import time
from itertools import islice

from .concurrency import SOURCE_LATENCY_SECONDS, gather_overview_sequential, gather_overview_concurrent
from .config import SAMPLE_DATA_PATH, TECHNICIANS_PATH, DEMO_LOG_PATH, DEMO_MAINTENANCE_LOG_PATH
from .context_managers import DiagnosticConnection
from .iterators import AlertHistory, iter_disconnected_components, lazy_disconnected_pipeline
from .maintenance import MaintenanceDesk
from .models import Camera, MaintenanceRequest, Microphone, Workstation
from .processing import AlertManager, run_diagnostics_and_log_alerts
from .reports import (
    validate_and_assign_request,
    find_unknown_technician_references,
    generate_technician_workload_report,
)
from .repository import delete_log, load_workstations_from_jsonl, load_technicians_from_jsonl, load_maintenance_history


def run_demo():
    print("=" * 70)
    _header(1, "Loading synthetic workstation fleet from JSONL", first=True)
    workstations = demo_load_fleet()

    _header(2, "Composition: building a workstation out of components")
    demo_composition()

    _header(3, "Polymorphic diagnostics inside DiagnosticConnection")
    alert_manager = demo_polymorphic_diagnostics(workstations)

    _header(4, "Encapsulation and safe diagnostic connections")
    demo_encapsulation_and_context_manager(workstations)

    _header(5, "Data-structure operations")
    demo_data_structures(alert_manager)

    _header(6, "Draining the queues")
    demo_queues(alert_manager)

    _header(7, "Custom Iterable/Iterator over unresolved alerts")
    demo_iterators(alert_manager)

    _header(8, "Generator yielding disconnected components")
    demo_generator(workstations)

    _header(9, "3-step lazy pipeline: disconnected -> older than 3 years -> id (first 2 only)")
    demo_lazy_pipeline(workstations, limit=2, min_age_years=3)

    _header(10, "Maintenance calls opened and assigned to technicians")
    demo_maintenance_workflow(alert_manager, workstations)

    _header(11, "Async information gathering: three independent sources with create_task + gather")
    demo_async_overview()
    print("=" * 70)


def _header(number, title, first=False):
    separator = "" if first else "\n"
    print(f"{separator}STEP {number} — {title}")


def demo_load_fleet():
    workstations = load_workstations_from_jsonl(SAMPLE_DATA_PATH)
    for workstation in workstations:
        print(f"  {workstation}")
    return workstations


def demo_composition():
    """Uses every Workstation method on a bench workstation built here, so
    the loaded fleet stays untouched for the later steps."""
    bench = Workstation("BENCH", "Repair Bench", "IT room")
    bench.add_component(Camera("CAM-B1", "Bench Cam", "BENCH"))
    bench.add_component(Camera("CAM-B2", "Broken Cam", "BENCH", resolution="0x0"))
    bench.add_component(Microphone("MIC-B1", "Bench Mic", "BENCH"))
    print(f"  Built: {bench}")
    print(f"  find_component('MIC-B1'): {bench.find_component('MIC-B1')}")
    print(f"  find_component('MIC-404'): {bench.find_component('MIC-404')}")
    cameras = bench.find_components_by_type("Camera")
    print(f"  find_components_by_type('Camera'): {[c.component_id for c in cameras]}")
    print(f"  run_all_diagnostics() (polymorphic, no type checks): {bench.run_all_diagnostics()}")
    removed = bench.remove_component("CAM-B2")
    print(f"  remove_component('CAM-B2') -> {removed}; "
          f"removing it again -> {bench.remove_component('CAM-B2')}")
    print(f"  After removal: {bench}")


def demo_polymorphic_diagnostics(workstations):
    # the demo fleet is synthetic: its alerts go to a separate log, reset every run
    delete_log(DEMO_LOG_PATH)
    alert_manager = AlertManager(log_path=DEMO_LOG_PATH)
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
    print("  Normal run:")
    with DiagnosticConnection(spare) as tested:
        print(f"  Testing {tested.component_id} (status {tested.connection_status}) -> "
              f"healthy={tested.run_diagnostics()}")
    print(f"  After a normal run: status {spare.connection_status}, "
          f"free ports {DiagnosticConnection.available_ports()}")

    print("  Run that raises inside the with block:")
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
    print("  Alerts sorted by priority, then time (sorted + lambda returning a tuple):")
    for alert in alert_manager.sorted_by_priority():
        print(f"    {alert}")
    print("  Alerts sorted by workstation, then priority (sorted + named key function):")
    for alert in alert_manager.sorted_by_workstation():
        print(f"    #{alert.alert_id} {alert.workstation_id} priority {alert.priority}")

    most_urgent, the_rest = alert_manager.most_urgent_and_rest()
    print(f"  Star unpacking: handle #{most_urgent.alert_id if most_urgent else '-'} now, "
          f"{len(the_rest)} can wait")

    print("  Workstations with no camera AND no microphone (set &):",
          alert_manager.workstations_without_audio_and_video() or "none")


def demo_queues(alert_manager):
    print("  Help desk acknowledges alerts in arrival order (deque.popleft, FIFO):")
    for _ in range(3):
        alert = alert_manager.acknowledge_next()
        if alert is None:
            break
        print(f"    acknowledged #{alert.alert_id} (priority {alert.priority})")
    print("  Acknowledged ids (set.add):", sorted(alert_manager.acknowledged_alert_ids))
    if alert_manager.acknowledged_alert_ids:
        undone = max(alert_manager.acknowledged_alert_ids)
        alert_manager.unacknowledge_alert(undone)
        alert_manager.unacknowledge_alert(undone)  # discard again: no error
        print(f"  Acknowledgement of #{undone} undone twice with set.discard (no KeyError)")
    print("  Still to acknowledge (set difference):", sorted(alert_manager.unacknowledged_alert_ids()))

    print("  Technicians dispatched by urgency (heapq.heappop, lower number = more urgent):")
    first_dispatched = None
    while (alert := alert_manager.dispatch_next()) is not None:
        first_dispatched = first_dispatched or alert
        print(f"    dispatch #{alert.alert_id} priority {alert.priority} ({alert.component_id})")
    print(f"  Dispatch queue empty: dispatch_next() returned {alert_manager.dispatch_next()}")
    while alert_manager.acknowledge_next() is not None:
        pass
    print(f"  Intake queue empty: acknowledge_next() returned {alert_manager.acknowledge_next()}")

    if first_dispatched is not None:
        first_dispatched.resolved = True
        print(f"  Alert #{first_dispatched.alert_id} fixed on site and marked resolved.")


def demo_iterators(alert_manager):
    history = AlertHistory(alert_manager.alert_history)
    iterator = iter(history)
    print(f"  iter(AlertHistory) returned an {type(iterator).__name__}")
    shown = []
    for alert in iterator:
        print(f"  Unresolved: {alert}")
        shown.append(alert.alert_id)
    print(f"  Skipped {len(history) - len(shown)} resolved alert(s) transparently.")
    listed = [a.alert_id for a in alert_manager.unresolved_alerts()]
    print(f"  The list comprehension unresolved_alerts() gives the same ids: {listed == shown}")

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
    disconnected = iter_disconnected_components(workstations)
    print(f"  Created {type(disconnected).__name__}; no component has been checked yet")
    print(f"  next(): {next(disconnected)}")
    print("  for loop continues from where next() stopped:")
    for component in disconnected:
        print(f"    {component}")
    print(f"  Second pass over the same generator yields {len(list(disconnected))} items (exhausted)")
    fresh = iter_disconnected_components(workstations)
    print(f"  A new generator starts over: first item is {next(fresh).component_id}")


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


def demo_maintenance_workflow(alert_manager, workstations):
    """Loads the technicians, opens a maintenance call for every unresolved
    alert (auto-assigned to a technician with the fewest calls, and saved to the
    demo maintenance log), resolves one, and prints the workload report.

    The demo fleet is synthetic, so its calls go to a separate log that is
    reset every run and never reach the real maintenance history."""
    delete_log(DEMO_MAINTENANCE_LOG_PATH)
    technicians = load_technicians_from_jsonl(TECHNICIANS_PATH)

    print(f"  Loaded {len(technicians)} technician(s); any of them can take any call.")

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


def demo_async_overview():
    """Collects the fleet, the technician roster and the demo maintenance log
    (filled by step 10) first one source at a time, then all three at once,
    and compares how long each took."""
    sources = (SAMPLE_DATA_PATH, TECHNICIANS_PATH, DEMO_MAINTENANCE_LOG_PATH)
    print(f"  Each source takes about {SOURCE_LATENCY_SECONDS:.1f} s to answer (simulated latency).")

    started = time.perf_counter()
    sequential = asyncio.run(gather_overview_sequential(*sources))
    sequential_seconds = time.perf_counter() - started

    started = time.perf_counter()
    overview = asyncio.run(gather_overview_concurrent(*sources))
    concurrent_seconds = time.perf_counter() - started

    print(f"  One after another (await each): {sequential_seconds:.2f} s")
    print(f"  All at once (create_task + gather): {concurrent_seconds:.2f} s")
    print(f"  Same overview either way: {sequential == overview}")

    fleet, maintenance = overview["fleet"], overview["maintenance"]
    print(f"  Fleet: {fleet['workstations']} workstation(s), components by status {fleet['components_by_status']}")
    print(f"  Technicians: {', '.join(overview['technicians'])}")
    print(f"  Maintenance: {maintenance['total_calls']} call(s), still open: {maintenance['open_calls'] or 'none'}")
