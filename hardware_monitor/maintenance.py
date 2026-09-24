"""Maintenance desk: turns alerts into MaintenanceRequest calls, assigns each
call to the least-busy technician with the matching specialty, and appends
every state change to logs/maintenance_requests.jsonl so the history of who
handled what survives past this process. The CSV report is refreshed on every
change, so it is current even if the program is killed rather than closed."""

import json
import os
import threading

from .config import MAINTENANCE_LOG_PATH, MAINTENANCE_REPORT_CSV_PATH
from .models import MaintenanceRequest
from .reports import validate_and_assign_request, export_maintenance_history_csv
from .repository import load_maintenance_history

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
        self._next_number = self._last_request_number() + 1

    def _last_request_number(self):
        """Continues numbering after the highest request id already in the
        log, so ids stay unique across runs."""
        numbers = [
            int(record["request_id"].split("-")[1])
            for record in load_maintenance_history(self.log_path)
        ]
        return max(numbers, default=0)

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

        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(record, ensure_ascii=False) + "\n")

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
