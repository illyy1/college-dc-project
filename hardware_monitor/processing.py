"""Data structures & operations: alert history (list), grouping (dict),
unique error types (set), FIFO maintenance queue (deque) and a critical
priority queue (heapq)."""

import heapq
from collections import deque
from datetime import datetime
from itertools import count

from .config import LOG_PATH
from .context_managers import DiagnosticConnection
from .repository import append_jsonl_record, iter_jsonl_records


def _next_alert_id():
    """Continues after the highest alert id in the history log, so alert ids
    (also stored on maintenance calls) stay unique across runs."""
    ids = (r.get("alert_id") for r in iter_jsonl_records(LOG_PATH))
    return max((i for i in ids if isinstance(i, int)), default=0) + 1


_id_counter = count(_next_alert_id())


class Alert:
    def __init__(self, component_id, workstation_id, error_type, message,
                 priority=5, resolved=False, timestamp=None):
        self.alert_id = next(_id_counter)
        self.component_id = component_id
        self.workstation_id = workstation_id
        self.error_type = error_type
        self.message = message
        self.priority = priority  # lower number = more critical
        self.resolved = resolved
        self.timestamp = timestamp or datetime.now()

    def to_dict(self):
        return {
            "alert_id": self.alert_id,
            "component_id": self.component_id,
            "workstation_id": self.workstation_id,
            "error_type": self.error_type,
            "message": self.message,
            "priority": self.priority,
            "resolved": self.resolved,
            "timestamp": self.timestamp.isoformat(),
        }

    def __repr__(self):
        state = "resolved" if self.resolved else "unresolved"
        return f"<Alert #{self.alert_id} [{self.error_type}] prio={self.priority} {state}>"

    def __str__(self):
        state = "RESOLVED" if self.resolved else "OPEN"
        return (f"Alert #{self.alert_id} [{state}] priority {self.priority} — "
                f"{self.error_type} on {self.component_id} @ {self.workstation_id}: {self.message}")


class AlertManager:
    """Owns the alert history plus the routine/critical processing queues."""

    CRITICAL_PRIORITY_THRESHOLD = 2

    def __init__(self):
        self.alert_history = []            # list — ordered history
        self.maintenance_queue = deque()   # deque — FIFO routine maintenance
        self._critical_heap = []           # heapq — priority queue of critical alerts
        self._heap_tiebreak = count()
        self.acknowledged_alert_ids = set()  # set — alert ids a technician has acknowledged

    def log_alert(self, alert, persist=True):
        self.alert_history.append(alert)
        if alert.priority <= self.CRITICAL_PRIORITY_THRESHOLD:
            heapq.heappush(self._critical_heap, (alert.priority, next(self._heap_tiebreak), alert))
        else:
            self.maintenance_queue.append(alert)
        if persist:
            self._persist_to_log(alert)
        return alert

    def process_next_critical(self):
        if not self._critical_heap:
            return None
        _, _, alert = heapq.heappop(self._critical_heap)
        return alert

    def process_next_maintenance(self):
        if not self.maintenance_queue:
            return None
        return self.maintenance_queue.popleft()

    def acknowledge_alert(self, alert_id):
        """set.add: marks an alert as seen by a technician."""
        self.acknowledged_alert_ids.add(alert_id)

    def unacknowledge_alert(self, alert_id):
        """set.discard: reverts an acknowledgement (no-op if it wasn't set)."""
        self.acknowledged_alert_ids.discard(alert_id)

    def critical_and_routine_common_error_types(self):
        """set intersection: error types currently sitting in BOTH the
        critical heap and the routine maintenance queue at the same time —
        useful to spot a systemic issue rather than an isolated one."""
        critical_types = {alert.error_type for _, _, alert in self._critical_heap}
        routine_types = {alert.error_type for alert in self.maintenance_queue}
        return critical_types & routine_types

    def group_by_workstation(self):
        """dict comprehension: workstation_id -> list of its alerts."""
        return {
            ws_id: [a for a in self.alert_history if a.workstation_id == ws_id]
            for ws_id in {a.workstation_id for a in self.alert_history}
        }

    def unique_error_types(self):
        """set comprehension: every distinct error type seen so far."""
        return {a.error_type for a in self.alert_history}

    def unresolved_alerts(self):
        return [a for a in self.alert_history if not a.resolved]

    def sorted_by_priority(self):
        """sorted() with a lambda key: most critical (lowest priority) first,
        ties broken by earliest timestamp."""
        return sorted(self.alert_history, key=lambda a: (a.priority, a.timestamp))

    def _persist_to_log(self, alert):
        """Appends the alert to the on-disk history log using a context
        manager, so the record survives past this process's lifetime."""
        append_jsonl_record(LOG_PATH, alert.to_dict())


def run_diagnostics_and_log_alerts(workstations, alert_manager):
    """Polymorphically tests every component inside a DiagnosticConnection
    and logs an Alert for anything unhealthy."""
    for workstation in workstations:
        for component in workstation.components:
            try:
                with DiagnosticConnection(component, verbose=False) as tested:
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
