"""Orchestrator only — no class definitions live here.

Loads the synthetic workstation fleet from data/sample_data.jsonl, runs a
linear demonstration of the OOP model, data structures/queues, iterators,
lazy pipeline and context manager working together, then launches the live
hardware-monitoring dashboard for this machine.
"""

from hardware_monitor.config import SAMPLE_DATA_PATH
from hardware_monitor.repository import load_workstations_from_jsonl
from hardware_monitor.processing import Alert, AlertManager
from hardware_monitor.iterators import (
    AlertHistory,
    iter_disconnected_components,
    lazy_disconnected_pipeline,
)
from hardware_monitor.context_managers import HardwareDiagnosticSession
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

    print("=" * 70)


def main():
    demonstrate_information_system()

    print("\nLaunching live hardware monitoring dashboard for this workstation...")
    app = HardwareMonitorApp()
    app.start()


if __name__ == "__main__":
    main()
