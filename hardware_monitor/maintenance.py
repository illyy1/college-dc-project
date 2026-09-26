"""Maintenance desk: turns alerts into MaintenanceRequest calls, assigns each
call to the least-busy technician with the matching specialty, and appends
every state change to logs/maintenance_requests.jsonl so the history of who
handled what survives past this process. The CSV report is refreshed on every
change, so it is current even if the program is killed rather than closed."""

import threading
from datetime import datetime

from .config import MAINTENANCE_LOG_PATH, MAINTENANCE_REPORT_CSV_PATH
from .models import MaintenanceRequest
from .reports import validate_and_assign_request, export_maintenance_history_csv
from .repository import append_jsonl_record, load_maintenance_history, request_number

# error_type prefix -> technician specialty
ERROR_PREFIX_TO_SPECIALTY = {
    "CAMERA": "Camera",
    "MICROPHONE": "Microphone",
}


def specialty_for_error_type(error_type):
    prefix = error_type.split("_", 1)[0].upper()
    return ERROR_PREFIX_TO_SPECIALTY.get(prefix, "General")


class MaintenanceDesk:
    def __init__(self, technicians, log_path=MAINTENANCE_LOG_PATH,
                 report_csv_path=MAINTENANCE_REPORT_CSV_PATH):
        self.technicians = technicians
        self.log_path = log_path
        self.report_csv_path = report_csv_path
        self.requests = {}
        self._lock = threading.Lock()  # the dashboard opens/resolves calls from several threads

        history = load_maintenance_history(self.log_path)
        # continue numbering after the highest id already in the log, so ids stay unique across runs
        self._next_number = max((request_number(r["request_id"]) for r in history), default=0) + 1
        self._restore_open_requests(history)

    def _restore_open_requests(self, history):
        """Reloads calls a previous run left open, so they can still be
        resolved and still count towards their technician's workload."""
        for record in history:
            if record.get("status") == "RESOLVED":
                continue
            opened_at = record.get("opened_at")
            request = MaintenanceRequest(
                request_id=record["request_id"],
                alert_id=record.get("alert_id"),
                priority=record.get("priority", 5),
                error_type=record.get("error_type", ""),
                workstation_id=record.get("workstation_id", ""),
                component_id=record.get("component_id", ""),
                opened_at=datetime.fromisoformat(opened_at) if opened_at else None,
            )
            request.status = record.get("status", "OPEN")
            request.assigned_tech_id = record.get("assigned_tech_id")
            technician = self.technicians.get(request.assigned_tech_id)
            if technician is not None:
                technician.assigned_requests.append(request)
            else:
                # cross-file check: the log names a technician missing from technicians.jsonl
                print(f"Warning: open call {request.request_id} is assigned to unknown technician "
                      f"'{request.assigned_tech_id}' (not in the technician data file).")
            self.requests[request.request_id] = request

    def open_requests(self):
        return [r for r in self.requests.values() if r.status != "RESOLVED"]

    def find_open_requests(self, workstation_id, component_id, error_type):
        """Unresolved calls already open for this exact fault, oldest first."""
        return sorted(
            (r for r in self.open_requests()
             if (r.workstation_id, r.component_id, r.error_type) == (workstation_id, component_id, error_type)),
            key=lambda r: r.opened_at,
        )

    def pick_technician(self, specialty):
        """Least-busy technician with the given specialty; if there is none,
        falls back to the least-busy technician overall."""
        if not self.technicians:
            raise ValueError("No technicians are registered in the system.")

        specialists = [t for t in self.technicians.values() if t.specialty == specialty]
        candidates = specialists or list(self.technicians.values())
        return min(
            candidates,
            key=lambda t: sum(1 for r in t.assigned_requests if r.status != "RESOLVED"),
        )

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

            technician = self.pick_technician(specialty_for_error_type(alert.error_type))
            validate_and_assign_request(request, technician.tech_id, self.technicians)
            self.requests[request.request_id] = request
            self._persist(request)
            return request

    def resolve_request(self, request_id):
        with self._lock:
            request = self.requests.get(request_id)
            if request is None:
                raise ValueError(f"Maintenance request '{request_id}' does not exist.")
            if request.status == "RESOLVED":
                return request
            request.resolve()
            self._persist(request)
            return request

    def technician_for(self, request):
        return self.technicians.get(request.assigned_tech_id)

    def _persist(self, request):
        record = request.to_dict()
        technician = self.technician_for(request)
        record["assigned_tech_name"] = technician.name if technician else None

        append_jsonl_record(self.log_path, record)

        self._refresh_csv_report()

    def _refresh_csv_report(self):
        if self.report_csv_path is None:
            return
        try:
            export_maintenance_history_csv(self.log_path, self.report_csv_path, verbose=False)
        except PermissionError:
            # usually the CSV is open in Excel; the JSONL log still has the call,
            # and the CSV catches up on the next change or `main.py --report`
            print(f"Warning: could not update {self.report_csv_path} (is it open in Excel?). "
                  f"The call was still saved to {self.log_path}.")
