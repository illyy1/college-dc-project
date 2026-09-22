"""Room hardware monitoring package: OOP hardware model, alert data
structures/queues, iterators, a lazy pipeline, a diagnostic context manager,
and the live monitoring dashboard."""

from .models import HardwareComponent, Camera, Microphone, Workstation
from .processing import Alert, AlertManager
from .iterators import (
    AlertHistory,
    ActiveAlertIterator,
    iter_disconnected_components,
    lazy_disconnected_pipeline,
)
from .context_managers import HardwareDiagnosticSession
from .repository import load_workstations_from_jsonl
from .dashboard import HardwareMonitorApp

__all__ = [
    "HardwareComponent",
    "Camera",
    "Microphone",
    "Workstation",
    "Alert",
    "AlertManager",
    "AlertHistory",
    "ActiveAlertIterator",
    "iter_disconnected_components",
    "lazy_disconnected_pipeline",
    "HardwareDiagnosticSession",
    "load_workstations_from_jsonl",
    "HardwareMonitorApp",
]
