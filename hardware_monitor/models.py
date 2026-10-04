"""OOP core: HardwareComponent hierarchy (inheritance/polymorphism), the
Workstation class, which composes components (composition), and the
Technician / MaintenanceRequest classes used for maintenance workflow."""

from datetime import date, datetime


def _require_fields(record, fields, kind):
    """Raises ValueError naming every field in `fields` that is missing, null or blank."""
    if not isinstance(record, dict):
        raise ValueError(f"{kind} record must be a JSON object, got {type(record).__name__}.")
    # null is checked first: str(None) is "None", which would pass as filled in
    missing = [f for f in fields if record.get(f) is None or not str(record[f]).strip()]
    if missing:
        raise ValueError(f"{kind} record is missing {', '.join(missing)}.")


def _check_number(record, field, kind, integer=False):
    """Raises ValueError if `field` is present but not a number (bools rejected)."""
    if field not in record:
        return
    value = record[field]
    allowed = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, allowed):
        expected = "an integer" if integer else "a number"
        raise ValueError(f"{kind} field '{field}' must be {expected}, got {value!r}.")


class HardwareComponent:
    """Base class for any piece of monitored hardware."""

    ALLOWED_STATUSES = {"ACTIVE", "DISCONNECTED", "TESTING", "ERROR", "MAINTENANCE"}
    REQUIRED_FIELDS = ("component_type", "component_id", "workstation_id")
    RECORD_FIELDS = ()  # subclass-specific keys from_dict() passes to __init__

    def __init__(self, component_id, name, workstation_id, connection_status="ACTIVE", installed_on=None):
        self.component_id = component_id
        self.name = name
        self.workstation_id = workstation_id
        self._connection_status = None
        self.connection_status = connection_status  # goes through the validating setter
        self._status_before_test = None  # set while a DiagnosticConnection is open
        self.installed_on = date.fromisoformat(installed_on) if isinstance(installed_on, str) else installed_on

    @classmethod
    def from_dict(cls, record):
        """Factory: validates a parsed JSONL record and builds the matching
        subclass. Called on HardwareComponent it picks Camera/Microphone from
        record["component_type"]; called on a subclass it builds that class.
        Raises ValueError on a missing field, unknown type or bad value."""
        _require_fields(record, cls.REQUIRED_FIELDS, "Component")

        component_type = str(record["component_type"]).strip().lower()
        if cls is HardwareComponent:
            subclasses = {sub.__name__.lower(): sub for sub in cls.__subclasses__()}
            if component_type not in subclasses:
                raise ValueError(
                    f"Unknown component_type '{record['component_type']}'. "
                    f"Must be one of {sorted(subclasses)}."
                )
            return subclasses[component_type].from_dict(record)
        if component_type != cls.__name__.lower():
            raise ValueError(f"{cls.__name__}.from_dict() got a '{record['component_type']}' record.")

        installed_on = record.get("installed_on")
        if installed_on is not None and not isinstance(installed_on, str):
            raise ValueError(f"Component '{record['component_id']}': installed_on must be a date "
                             f"string like '2021-05-30', got {installed_on!r}.")
        cls._validate_record(record)
        try:
            return cls(
                component_id=record["component_id"],
                name=record.get("name") or record["component_id"],
                workstation_id=record["workstation_id"],
                connection_status=record.get("connection_status", "ACTIVE"),
                installed_on=record.get("installed_on"),
                **{key: record[key] for key in cls.RECORD_FIELDS if key in record},
            )
        except ValueError as error:  # invalid status or installed_on date
            raise ValueError(f"Component '{record['component_id']}': {error}") from None

    @classmethod
    def _validate_record(cls, record):
        """Subclass hook for type checks on subclass-specific fields."""

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

    FAULT_STATUSES = {"DISCONNECTED", "ERROR", "MAINTENANCE"}

    def _has_fault_status(self):
        """True if the component is disconnected, in error or under maintenance,
        looking past a TESTING lock."""
        status = self.connection_status
        if status == "TESTING" and self._status_before_test is not None:
            status = self._status_before_test
        return status in self.FAULT_STATUSES

    def run_diagnostics(self):
        """Overridden by subclasses. Returns True if the component is healthy."""
        raise NotImplementedError("Subclasses must implement run_diagnostics().")

    def alert_priority(self):
        """Overridden by subclasses: how urgent an alert about this failed
        component is (lower number = more urgent)."""
        raise NotImplementedError("Subclasses must implement alert_priority().")

    def __repr__(self):
        return f"<{self.component_type} id={self.component_id} status={self.connection_status}>"

    def __str__(self):
        return f"{self.component_type} '{self.name}' ({self.component_id}) — {self.connection_status}"


class Camera(HardwareComponent):
    RECORD_FIELDS = ("camera_index", "resolution")

    def __init__(self, component_id, name, workstation_id, connection_status="ACTIVE",
                 camera_index=0, resolution="1920x1080", installed_on=None):
        super().__init__(component_id, name, workstation_id, connection_status, installed_on)
        self.camera_index = camera_index
        self.resolution = resolution

    @classmethod
    def _validate_record(cls, record):
        # A "0x0" resolution is allowed on purpose: it is a faulty camera that
        # run_diagnostics() should catch, not a malformed record.
        _check_number(record, "camera_index", "Camera", integer=True)
        if "resolution" in record and not isinstance(record["resolution"], str):
            raise ValueError(f"Camera field 'resolution' must be a string like '1920x1080', "
                             f"got {record['resolution']!r}.")

    @staticmethod
    def parse_resolution(resolution):
        """'1920x1080' -> (1920, 1080), or None if it is not a usable resolution."""
        try:
            width, height = (int(part) for part in resolution.lower().split("x"))
        except (ValueError, AttributeError):
            return None
        return (width, height) if width > 0 and height > 0 else None

    def run_diagnostics(self):
        """Camera-specific health check: valid resolution and no fault status."""
        resolution_ok = self.parse_resolution(self.resolution) is not None
        healthy = resolution_ok and not self._has_fault_status()
        self.connection_status = "ACTIVE" if healthy else "DISCONNECTED"
        return healthy

    def alert_priority(self):
        """1 = the camera itself is lost (meeting has no video at all);
        2 = it is connected but misconfigured (bad resolution), which can be
        fixed remotely."""
        return 2 if self.parse_resolution(self.resolution) is None else 1

    def __str__(self):
        return f"{super().__str__()} [camera #{self.camera_index}, {self.resolution}]"


class Microphone(HardwareComponent):
    MIN_SAMPLE_RATE = 8000
    RECORD_FIELDS = ("sample_rate", "sensitivity_db")

    def __init__(self, component_id, name, workstation_id, connection_status="ACTIVE",
                 sample_rate=44100, sensitivity_db=-40, installed_on=None):
        super().__init__(component_id, name, workstation_id, connection_status, installed_on)
        self.sample_rate = sample_rate
        self.sensitivity_db = sensitivity_db

    @classmethod
    def _validate_record(cls, record):
        # A too-low sample rate is allowed on purpose: run_diagnostics() flags it.
        _check_number(record, "sample_rate", "Microphone")
        _check_number(record, "sensitivity_db", "Microphone")

    def run_diagnostics(self):
        """Microphone-specific health check: sane sample rate and no fault status."""
        healthy = self._sample_rate_ok() and not self._has_fault_status()
        self.connection_status = "ACTIVE" if healthy else "DISCONNECTED"
        return healthy

    def alert_priority(self):
        """Audio faults rank below video faults: 3 = the microphone is lost;
        4 = it is connected but misconfigured (sample rate too low)."""
        return 4 if not self._sample_rate_ok() else 3

    def _sample_rate_ok(self):
        return isinstance(self.sample_rate, (int, float)) and self.sample_rate >= self.MIN_SAMPLE_RATE

    def __str__(self):
        return f"{super().__str__()} [{self.sample_rate} Hz, {self.sensitivity_db} dB]"


class Workstation:
    """Composes a set of HardwareComponent objects belonging to one machine."""

    def __init__(self, workstation_id, name, location=""):
        self.workstation_id = workstation_id
        self.name = name
        self.location = location
        self._components = []

    @classmethod
    def from_dict(cls, record):
        """Factory: builds a Workstation (without components) from a parsed
        JSONL record. Raises ValueError if workstation_id is missing."""
        _require_fields(record, ("workstation_id",), "Workstation")
        return cls(
            workstation_id=record["workstation_id"],
            name=record.get("workstation_name") or record["workstation_id"],
            location=record.get("location", ""),
        )

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
    REQUIRED_FIELDS = ("tech_id", "name", "specialty")

    def __init__(self, tech_id: str, name: str, specialty: str, workstation_ids=()):
        self.tech_id = tech_id
        self.name = name
        self.specialty = specialty
        self.workstation_ids = tuple(workstation_ids)  # workstations (from sample_data.jsonl) this technician covers
        self.assigned_requests = []  # רשימת בקשות התחזוקה המשויכות לטכנאי

    @classmethod
    def from_dict(cls, record):
        """Factory: builds a Technician from a parsed JSONL record. Raises
        ValueError if a required field is missing or blank, or if
        workstation_ids is not a list of ids."""
        _require_fields(record, cls.REQUIRED_FIELDS, "Technician")
        workstation_ids = record.get("workstation_ids", [])
        if not isinstance(workstation_ids, list) or not all(isinstance(w, str) for w in workstation_ids):
            raise ValueError(f"Technician '{record['tech_id']}': workstation_ids must be a list of "
                             f"workstation ids like [\"WS-101\"], got {workstation_ids!r}.")
        return cls(
            tech_id=record["tech_id"],
            name=record["name"],
            specialty=record["specialty"],
            workstation_ids=workstation_ids,
        )

    def covers(self, workstation_id):
        """True if this technician is responsible for the given workstation."""
        return workstation_id in self.workstation_ids

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
