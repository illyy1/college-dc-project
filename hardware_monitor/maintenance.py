"""Maintenance desk: turns alerts into MaintenanceRequest calls, assigns each
call to a technician, and appends every state change to
logs/maintenance_requests.jsonl so the history of who handled what survives
past this process. The CSV report is refreshed on every change, so it is
current even if the program is killed rather than closed.

Which technician gets a call is a Strategy, in assignment.py: the desk
hands every technician to an AssignmentStrategy object, so the rule can be
swapped without touching the desk. The strategies are imported here too, so
`from hardware_monitor.maintenance import FewestCallsStrategy` still works."""

import logging
import threading
from collections import Counter
from datetime import datetime

from .assignment import AssignmentStrategy, FewestCallsStrategy, LeastBusyStrategy, RoundRobinStrategy
from .config import MAINTENANCE_LOG_PATH, MAINTENANCE_REPORT_CSV_PATH
from .models import MaintenanceRequest
from .reports import validate_and_assign_request, export_maintenance_history_csv
from .repository import append_jsonl_record, highest_request_number, load_maintenance_history

logger = logging.getLogger(__name__)

# the strategies are re-exported: callers have always found them here
__all__ = ["MaintenanceDesk", "AssignmentStrategy", "FewestCallsStrategy", "LeastBusyStrategy",
           "RoundRobinStrategy"]


class MaintenanceDesk:
    def __init__(self, technicians, log_path=MAINTENANCE_LOG_PATH,
                 report_csv_path=MAINTENANCE_REPORT_CSV_PATH, assignment_strategy=None):
        self.technicians = technicians
        self.assignment_strategy = assignment_strategy or FewestCallsStrategy()
        self.log_path = log_path
        self.report_csv_path = report_csv_path
        self.requests = {}
        self._lock = threading.Lock()  # the dashboard opens/resolves calls from several threads

        reported = set()  # the log is read twice here; each broken line is reported once
        history = load_maintenance_history(self.log_path, reported)
        # continue numbering after the highest id already in the log (skipped calls included),
        # so ids stay unique across runs
        self._next_number = highest_request_number(self.log_path, reported) + 1
        # tech_id -> when they were last given a call; the alternative strategies take turns by this
        self._last_assigned_at = {}
        # tech_id -> calls given to them, ever: what FewestCallsStrategy evens out
        self._calls_taken = Counter()
        for call in history:
            tech_id, when = call.assigned_tech_id, call.opened_at
            if tech_id:
                self._calls_taken[tech_id] += 1
            if tech_id and when:
                self._last_assigned_at[tech_id] = max(when, self._last_assigned_at.get(tech_id, when))
        self._restore_open_requests(history)

    def _restore_open_requests(self, history):
        """Reloads calls a previous run left open, so they can still be
        resolved and still count towards their technician's workload."""
        for request in history:
            if request.status == "RESOLVED":
                continue
            if request.opened_at is None:
                # an open call must have an open time: the desk sorts open calls by it
                request.opened_at = datetime.now()
            technician = self.technicians.get(request.assigned_tech_id)
            if technician is not None:
                technician.assigned_requests.append(request)
            else:
                # cross-file check: the log names a technician missing from technicians.jsonl
                logger.warning(f"Open call {request.request_id} is assigned to unknown technician "
                      f"'{request.assigned_tech_id}' (not in the technician data file).")
            self.requests[request.request_id] = request

    def open_requests(self):
        with self._lock:
            return [r for r in self.requests.values() if r.status != "RESOLVED"]

    def find_open_requests(self, workstation_id, component_id, error_type):
        """Unresolved calls already open for this exact fault, oldest first."""
        return sorted(
            (r for r in self.open_requests()
             if (r.workstation_id, r.component_id, r.error_type) == (workstation_id, component_id, error_type)),
            key=lambda r: r.opened_at,
        )

    def pick_technician(self):
        """Every technician can take any call, so all of them are candidates;
        the assignment strategy picks one."""
        if not self.technicians:
            raise ValueError("No technicians are registered in the system.")
        return self.assignment_strategy.choose(list(self.technicians.values()), self._last_assigned_at,
                                               self._calls_taken)

    def open_request_for_alert(self, alert):
        """Opens a call for the alert, assigns it to a technician and logs it."""
        with self._lock:
            request = MaintenanceRequest(
                request_id=f"MR-{self._next_number:04d}",
                alert_id=alert.alert_id,
                priority=alert.priority,
                error_type=alert.error_type,
                workstation_id=alert.workstation_id,
                component_id=alert.component_id,
            )
            self._next_number += 1

            technician = self.pick_technician()
            validate_and_assign_request(request, technician.tech_id, self.technicians)
            self._last_assigned_at[technician.tech_id] = request.opened_at
            self._calls_taken[technician.tech_id] += 1
            self.requests[request.request_id] = request
            self._persist(request)
            return request

    def resolve_request(self, request_id):
        with self._lock:
            try:
                request = self.requests[request_id]
            except KeyError:
                raise ValueError(f"Maintenance request '{request_id}' does not exist.") from None
            if request.status == "RESOLVED":
                return request
            request.resolve()
            self._persist(request)
            return request

    def update_request(self, request_id, change):
        """Applies change(call) to a call under the desk's lock, then saves it.
        The desk only stores: what to change is decided elsewhere (e.g.
        services.apply_triage). Raises ValueError for an unknown call."""
        with self._lock:
            try:
                request = self.requests[request_id]
            except KeyError:
                raise ValueError(f"Maintenance request '{request_id}' does not exist.") from None
            change(request)
            self._persist(request)
            return request

    def technician_for(self, request):
        return self.technicians.get(request.assigned_tech_id)

    def _persist(self, request):
        record = request.to_dict()
        technician = self.technician_for(request)
        record["assigned_tech_name"] = technician.name if technician else None

        try:
            append_jsonl_record(self.log_path, record)
        except OSError as error:
            # the call is still open in this run and its technician keeps it; every
            # line holds the call's whole state, so its next change saves it in full
            logger.warning(f"Could not save call {request.request_id} to {self.log_path} ({error}). "
                  f"It is kept in this run, and saved in full on its next change.")
            return

        self._refresh_csv_report()

    def _refresh_csv_report(self):
        if self.report_csv_path is None:
            return
        try:
            export_maintenance_history_csv(self.log_path, self.report_csv_path, verbose=False)
        except OSError as error:
            # usually the CSV is open in Excel; the JSONL log still has the call,
            # and the CSV catches up on the next change or `main.py --report`
            reason = "is it open in Excel?" if isinstance(error, PermissionError) else error
            logger.warning(f"Could not update {self.report_csv_path} ({reason}). "
                  f"The call was still saved to {self.log_path}.")
