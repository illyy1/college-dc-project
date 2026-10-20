"""Room hardware monitoring package: OOP hardware model, alert data
structures/queues, iterators, a lazy pipeline, a diagnostic context manager,
a maintenance desk that assigns calls to technicians, workload/history
reports, and the live monitoring dashboard."""

from .models import (
    HardwareComponent,
    Camera,
    Microphone,
    Workstation,
    Technician,
    MaintenanceRequest,
)
from .processing import Alert, AlertManager
from .iterators import (
    AlertHistory,
    ActiveAlertIterator,
    iter_disconnected_components,
    lazy_disconnected_pipeline,
)
from .context_managers import DiagnosticConnection
from .repository import (
    load_workstations_from_jsonl,
    load_technicians_from_jsonl,
    load_maintenance_history,
)
from .reports import (
    validate_and_assign_request,
    find_unknown_technician_references,
    find_unknown_workstation_references,
    generate_technician_workload_report,
    generate_maintenance_history_report,
    export_maintenance_history_csv,
)
from .maintenance import MaintenanceDesk, AssignmentStrategy, LeastBusyStrategy, RoundRobinStrategy
from .concurrency import (
    fetch_statuses_sequential,
    fetch_statuses_concurrent,
    gather_overview_sequential,
    gather_overview_concurrent,
    LockedAlertLog,
    record_alerts_from_threads,
)

__all__ = [
    "HardwareComponent",
    "Camera",
    "Microphone",
    "Workstation",
    "Technician",
    "MaintenanceRequest",
    "Alert",
    "AlertManager",
    "AlertHistory",
    "ActiveAlertIterator",
    "iter_disconnected_components",
    "lazy_disconnected_pipeline",
    "DiagnosticConnection",
    "load_workstations_from_jsonl",
    "load_technicians_from_jsonl",
    "load_maintenance_history",
    "validate_and_assign_request",
    "find_unknown_technician_references",
    "find_unknown_workstation_references",
    "generate_technician_workload_report",
    "generate_maintenance_history_report",
    "export_maintenance_history_csv",
    "MaintenanceDesk",
    "AssignmentStrategy",
    "LeastBusyStrategy",
    "RoundRobinStrategy",
    "fetch_statuses_sequential",
    "fetch_statuses_concurrent",
    "gather_overview_sequential",
    "gather_overview_concurrent",
    "LockedAlertLog",
    "record_alerts_from_threads",
    "HardwareMonitorApp",
]


def __getattr__(name):
    # the dashboard needs tkinter, so it is only imported when actually used
    if name == "HardwareMonitorApp":
        from .dashboard import HardwareMonitorApp
        return HardwareMonitorApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
