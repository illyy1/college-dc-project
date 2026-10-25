"""Data structures & operations: alert history (list), grouping (dict),
unique error types and acknowledgements (set), a FIFO help-desk intake queue
(deque) and a technician dispatch priority queue (heapq)."""

import heapq
import threading
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


def workstation_then_priority(alert):
    """Sort key (a plain named function): groups alerts by workstation, most
    urgent first inside each workstation."""
    return alert.workstation_id, alert.priority


class AlertManager:
    """Owns the alert history plus the two processing queues every alert
    enters:

    - intake_queue (deque, FIFO): the help desk acknowledges alerts in the
      order they arrived, so the oldest alert is never left waiting behind
      newer ones and every fault gets an answer.
    - dispatch heap (heapq): technicians are sent to the most urgent fault
      first, whatever its arrival time. Lower priority number = more urgent;
      equal priorities leave in arrival order thanks to a counter tiebreak,
      so Alert objects themselves are never compared."""

    def __init__(self, log_path=None):
        self.log_path = log_path  # None = the real history log (LOG_PATH)
        self.alert_history = []            # list — ordered history
        self.intake_queue = deque()        # deque — FIFO help-desk acknowledgement
        self._dispatch_heap = []           # heapq — (priority, arrival_no, alert) tuples
        self._arrival_counter = count()
        self.acknowledged_alert_ids = set()  # set — alert ids the help desk has acknowledged
        self._lock = threading.Lock()  # the dashboard's mic and camera threads both log alerts

    def log_alert(self, alert, persist=True):
        with self._lock:
            self.alert_history.append(alert)
            self.intake_queue.append(alert)
            heapq.heappush(self._dispatch_heap, (alert.priority, next(self._arrival_counter), alert))
            if persist:
                self._persist_to_log(alert)
            return alert

    def dispatch_next(self):
        """heappop: the most urgent alert waiting for a technician, or None
        if nothing is waiting."""
        if not self._dispatch_heap:
            return None
        _, _, alert = heapq.heappop(self._dispatch_heap)
        return alert

    def acknowledge_next(self):
        """popleft: acknowledges the oldest alert waiting at the help desk,
        or returns None if the intake queue is empty."""
        if not self.intake_queue:
            return None
        alert = self.intake_queue.popleft()
        self.acknowledge_alert(alert.alert_id)
        return alert

    def acknowledge_alert(self, alert_id):
        """set.add: marks an alert as seen by the help desk."""
        self.acknowledged_alert_ids.add(alert_id)

    def unacknowledge_alert(self, alert_id):
        """set.discard: reverts an acknowledgement (no-op if it wasn't set,
        unlike set.remove, which would raise KeyError)."""
        self.acknowledged_alert_ids.discard(alert_id)

    def unacknowledged_alert_ids(self):
        """set difference: alerts the help desk still has to answer."""
        return {a.alert_id for a in self.alert_history} - self.acknowledged_alert_ids

    def workstations_without_audio_and_video(self):
        """set intersection: workstations with both a camera and a microphone
        alert. Such a room cannot host a video call at all."""
        camera_down = {a.workstation_id for a in self.alert_history if a.error_type == "CAMERA_FAILURE"}
        microphone_down = {a.workstation_id for a in self.alert_history if a.error_type == "MICROPHONE_FAILURE"}
        return camera_down & microphone_down

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

    def sorted_by_workstation(self):
        """sorted() with a named function as the key."""
        return sorted(self.alert_history, key=workstation_then_priority)

    def most_urgent_and_rest(self):
        """Star unpacking: splits the priority order into the alert to handle
        now and everything that can wait. Returns (None, []) with no alerts."""
        if not self.alert_history:
            return None, []
        most_urgent, *the_rest = self.sorted_by_priority()
        return most_urgent, the_rest

    def _persist_to_log(self, alert):
        """Appends the alert to the on-disk history log using a context
        manager, so the record survives past this process's lifetime."""
        append_jsonl_record(self.log_path or LOG_PATH, alert.to_dict())


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

            priority = tested.alert_priority()  # polymorphic: each subclass ranks its own faults
            alert = Alert(
                component_id=tested.component_id,
                workstation_id=workstation.workstation_id,
                error_type=f"{tested.component_type.upper()}_FAILURE",
                message=f"{tested.component_type} '{tested.name}' failed diagnostics.",
                priority=priority,
            )
            alert_manager.log_alert(alert)
