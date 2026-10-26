"""Maintenance desk: turns alerts into MaintenanceRequest calls, assigns each
call to a technician, and appends every state change to
logs/maintenance_requests.jsonl so the history of who handled what survives
past this process. The CSV report is refreshed on every change, so it is
current even if the program is killed rather than closed.

There are no specialists: every technician can take any call, of any kind,
on any workstation. Which one gets it is a Strategy: the desk hands every
technician to an AssignmentStrategy object, so the rule can be swapped
without touching the desk. The default, FewestCallsStrategy, draws at
random between everyone, favouring those with fewer calls saved in the log; LeastBusyStrategy and RoundRobinStrategy are alternatives."""

import random
import threading
from abc import ABC, abstractmethod
from collections import Counter
from datetime import datetime

from .config import MAINTENANCE_LOG_PATH, MAINTENANCE_REPORT_CSV_PATH
from .models import MaintenanceRequest
from .reports import validate_and_assign_request, export_maintenance_history_csv
from .repository import append_jsonl_record, highest_request_number, load_maintenance_history


def open_call_count(technician):
    return sum(1 for r in technician.assigned_requests if r.status != "RESOLVED")


class AssignmentStrategy(ABC):
    """Strategy interface: picks one technician from the candidates (every
    technician). last_assigned_at maps tech_id -> when they were last given a
    call; calls_taken maps tech_id -> how many calls they have been given in
    total, as saved in the log (technicians with none may be missing)."""

    @abstractmethod
    def choose(self, candidates, last_assigned_at, calls_taken):
        """Returns one technician from candidates."""


def _last_assigned(technician, last_assigned_at):
    return last_assigned_at.get(technician.tech_id, datetime.min)


class FewestCallsStrategy(AssignmentStrategy):
    """Default: a random draw between every technician, weighted towards
    fewer calls saved in the log (resolved ones and earlier runs included).
    Someone k calls ahead of the least-loaded technician weighs 1 / (1 + k),
    so anyone can still get the call, and the bias depends on the gap, not
    on the totals. rng: anything with a choices() method; Python's random
    by default."""

    def __init__(self, rng=random):
        self.rng = rng

    def choose(self, candidates, last_assigned_at, calls_taken):
        counts = [calls_taken.get(t.tech_id, 0) for t in candidates]
        fewest = min(counts)
        weights = [1 / (1 + count - fewest) for count in counts]
        return self.rng.choices(candidates, weights, k=1)[0]


class LeastBusyStrategy(AssignmentStrategy):
    """Alternative: fewest open calls; ties go to whoever was assigned least
    recently, so equally busy technicians take turns."""

    def choose(self, candidates, last_assigned_at, calls_taken):
        return min(candidates, key=lambda t: (open_call_count(t), _last_assigned(t, last_assigned_at)))


class RoundRobinStrategy(AssignmentStrategy):
    """Alternative: strict turns. Whoever was assigned least recently gets
    the call, however many open calls they have."""

    def choose(self, candidates, last_assigned_at, calls_taken):
        return min(candidates, key=lambda t: _last_assigned(t, last_assigned_at))


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
                print(f"Warning: open call {request.request_id} is assigned to unknown technician "
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

    def apply_triage(self, request_id, triage):
        """Applies Gemini's triage (gemini_client.Triage) to a call: the call
        takes the more urgent of its own priority (the hardware rule) and the
        triage's, so Gemini can raise urgency but never lower it; it keeps the
        likely cause and the estimated repair time; and it is saved."""
        with self._lock:
            try:
                request = self.requests[request_id]
            except KeyError:
                raise ValueError(f"Maintenance request '{request_id}' does not exist.") from None
            request.priority = min(request.priority, triage.priority)  # lower is more urgent
            request.cause = triage.cause
            request.estimated_minutes = triage.repair_minutes
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
            print(f"Warning: could not save call {request.request_id} to {self.log_path} ({error}). "
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
            print(f"Warning: could not update {self.report_csv_path} ({reason}). "
                  f"The call was still saved to {self.log_path}.")
