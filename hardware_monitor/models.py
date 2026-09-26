"""OOP core: HardwareComponent hierarchy (inheritance/polymorphism), the
Workstation class, which composes components (composition), and the
Technician / MaintenanceRequest classes used for maintenance workflow."""

from datetime import date, datetime


class HardwareComponent:
    """Base class for any piece of monitored hardware."""

    ALLOWED_STATUSES = {"ACTIVE", "DISCONNECTED", "TESTING", "ERROR"}

    def __init__(self, component_id, name, workstation_id, connection_status="ACTIVE", installed_on=None):
        self.component_id = component_id
        self.name = name
        self.workstation_id = workstation_id
        self._connection_status = None
        self.connection_status = connection_status  # goes through the validating setter
        self._status_before_test = None  # set while a DiagnosticConnection is open
        self.installed_on = date.fromisoformat(installed_on) if isinstance(installed_on, str) else installed_on

    @property
    def age_years(self):
        """Years since installation, or None if the install date is unknown."""
        if self.installed_on is None:
            return None
        return (date.today() - self.installed_on).days / 365.25

    @property
    def connection_status(self):
        return self._connection_status

    @connection_status.setter
    def connection_status(self, value):
        value = str(value).upper()
        if value not in self.ALLOWED_STATUSES:
            raise ValueError(
                f"Invalid connection status '{value}'. Must be one of {sorted(self.ALLOWED_STATUSES)}."
            )
        self._connection_status = value

    @property
    def component_type(self):
        return self.__class__.__name__

    def begin_test(self):
        """Remembers the real status and switches to TESTING."""
        self._status_before_test = self.connection_status
        self.connection_status = "TESTING"

    def end_test(self, final_status=None):
        """Leaves TESTING: uses final_status if given, otherwise keeps whatever
        run_diagnostics() set, or restores the pre-test status if it set nothing."""
        if final_status is not None:
            self.connection_status = final_status
        elif self.connection_status == "TESTING":
            self.connection_status = self._status_before_test
        self._status_before_test = None

    def _is_disconnected(self):
        """True if the component is disconnected, looking past a TESTING lock."""
        status = self.connection_status
        if status == "TESTING" and self._status_before_test is not None:
            status = self._status_before_test
        return status == "DISCONNECTED"

    def run_diagnostics(self):
        """Overridden by subclasses. Returns True if the component is healthy."""
        raise NotImplementedError("Subclasses must implement run_diagnostics().")

    def __str__(self):
        return f"{self.component_type} '{self.name}' ({self.component_id}) on {self.workstation_id} — {self.connection_status}"

    def __repr__(self):
        return f"<{self.component_type} id={self.component_id} status={self.connection_status}>"


class Camera(HardwareComponent):
    def __init__(self, component_id, name, workstation_id, connection_status="ACTIVE",
                 camera_index=0, resolution="1920x1080", installed_on=None):
        super().__init__(component_id, name, workstation_id, connection_status, installed_on)
        self.camera_index = camera_index
        self.resolution = resolution

    def run_diagnostics(self):
        """Camera-specific health check: valid resolution and not disconnected."""
        try:
            width, height = (int(part) for part in self.resolution.lower().split("x"))
            resolution_ok = width > 0 and height > 0
        except (ValueError, AttributeError):
            resolution_ok = False

        healthy = resolution_ok and not self._is_disconnected()
        self.connection_status = "ACTIVE" if healthy else "DISCONNECTED"
        return healthy

    def __str__(self):
        return f"{super().__str__()} [camera #{self.camera_index}, {self.resolution}]"


class Microphone(HardwareComponent):
    MIN_SAMPLE_RATE = 8000

    def __init__(self, component_id, name, workstation_id, connection_status="ACTIVE",
                 sample_rate=44100, sensitivity_db=-40, installed_on=None):
        super().__init__(component_id, name, workstation_id, connection_status, installed_on)
        self.sample_rate = sample_rate
        self.sensitivity_db = sensitivity_db

    def run_diagnostics(self):
        """Microphone-specific health check: sane sample rate and not disconnected."""
        rate_ok = isinstance(self.sample_rate, (int, float)) and self.sample_rate >= self.MIN_SAMPLE_RATE
        healthy = rate_ok and not self._is_disconnected()
        self.connection_status = "ACTIVE" if healthy else "DISCONNECTED"
        return healthy

    def __str__(self):
        return f"{super().__str__()} [{self.sample_rate} Hz, {self.sensitivity_db} dB]"


class Workstation:
    """Composes a set of HardwareComponent objects belonging to one machine."""

    def __init__(self, workstation_id, name, location=""):
        self.workstation_id = workstation_id
        self.name = name
        self.location = location
        self._components = []

    @property
    def components(self):
        """Read-only view — callers cannot mutate our internal list directly."""
        return list(self._components)

    def add_component(self, component):
        self._components.append(component)

    def remove_component(self, component_id):
        before = len(self._components)
        self._components = [c for c in self._components if c.component_id != component_id]
        return len(self._components) < before

    def find_component(self, component_id):
        return next((c for c in self._components if c.component_id == component_id), None)

    def find_components_by_type(self, component_type):
        return [c for c in self._components if c.component_type == component_type]

    def run_all_diagnostics(self):
        """Polymorphic sweep: calls run_diagnostics() on every component
        without ever checking what concrete type it is."""
        return {c.component_id: c.run_diagnostics() for c in self._components}

    def __str__(self):
        location = f" @ {self.location}" if self.location else ""
        return f"Workstation {self.workstation_id} '{self.name}'{location} — {len(self._components)} component(s)"

    def __repr__(self):
        return f"<Workstation {self.workstation_id} '{self.name}' components={len(self._components)}>"

# ( חבר 4: רחל )

class Technician:
    def __init__(self, tech_id: str, name: str, specialty: str):
        self.tech_id = tech_id
        self.name = name
        self.specialty = specialty
        self.assigned_requests = []  # רשימת בקשות התחזוקה המשויכות לטכנאי

    def __repr__(self):
        return f"Technician(id='{self.tech_id}', name='{self.name}', specialty='{self.specialty}')"

    def __str__(self):
        open_count = sum(1 for r in self.assigned_requests if r.status != "RESOLVED")
        return f"טכנאי: {self.name} | התמחות: {self.specialty} | משימות פתוחות: {open_count}"


class MaintenanceRequest:
    VALID_STATUSES = {"OPEN", "IN_PROGRESS", "RESOLVED"}

    def __init__(self, request_id: str, alert_id: int, priority: int,
                 error_type: str = "", workstation_id: str = "", component_id: str = "",
                 opened_at: datetime | None = None):
        self.request_id = request_id
        self.alert_id = alert_id
        self.priority = priority
        self.error_type = error_type
        self.workstation_id = workstation_id
        self.component_id = component_id
        self.opened_at = opened_at or datetime.now()
        self.resolved_at = None
        self._status = "OPEN"
        self.assigned_tech_id = None

    @property
    def status(self) -> str:
        return self._status

    @status.setter
    def status(self, new_status: str):
        new_status = str(new_status).upper()
        if new_status not in self.VALID_STATUSES:
            raise ValueError(f"סטטוס לא חוקי: {new_status}. סטטוסים מותרים: {self.VALID_STATUSES}")
        self._status = new_status

    def assign_to(self, tech_id: str):
        self.assigned_tech_id = tech_id
        self.status = "IN_PROGRESS"

    def resolve(self):
        self.status = "RESOLVED"
        self.resolved_at = datetime.now()

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "alert_id": self.alert_id,
            "priority": self.priority,
            "error_type": self.error_type,
            "workstation_id": self.workstation_id,
            "component_id": self.component_id,
            "status": self.status,
            "assigned_tech_id": self.assigned_tech_id,
            "opened_at": self.opened_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }

    def __repr__(self):
        return f"MaintenanceRequest(id='{self.request_id}', alert_id={self.alert_id}, status='{self.status}')"

    def __str__(self):
        return f"בקשת תחזוקה #{self.request_id} [עדיפות: {self.priority}] - סטטוס: {self.status}"
