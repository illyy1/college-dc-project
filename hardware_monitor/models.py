"""OOP core: HardwareComponent hierarchy (inheritance/polymorphism), the
Workstation class, which composes components (composition), and the
Technician / MaintenanceRequest classes used for maintenance workflow."""

from abc import ABC, abstractmethod
from datetime import date, datetime


def _require_fields(record, fields, kind):
    """Raises ValueError naming every field in `fields` that is missing, null
    or blank, or that is not a string (ids and names are always text, e.g. "WS-101")."""
    if not isinstance(record, dict):
        raise ValueError(f"{kind} record must be a JSON object, got {type(record).__name__}.")
    # null is checked first: str(None) is "None", which would pass as filled in
    missing = [f for f in fields if record.get(f) is None or not str(record[f]).strip()]
    if missing:
        raise ValueError(f"{kind} record is missing {', '.join(missing)}.")
    not_text = [f for f in fields if not isinstance(record[f], str)]
    if not_text:
        raise ValueError(f"{kind} field(s) {', '.join(not_text)} must be text, got "
                         f"{', '.join(repr(record[f]) for f in not_text)}.")


def _parse_time(record, field, kind):
    """`field` as a datetime, or None if it is missing or empty. Raises
    ValueError if it is there but not an ISO date and time."""
    value = record.get(field)
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{kind} field '{field}' must be an ISO date and time, got {value!r}.") from None


def _check_number(record, field, kind, integer=False):
    """Raises ValueError if `field` is present but not a number (bools rejected)."""
    if field not in record:
        return
    value = record[field]
    allowed = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, allowed):
        expected = "an integer" if integer else "a number"
        raise ValueError(f"{kind} field '{field}' must be {expected}, got {value!r}.")


class HardwareComponent(ABC):
    """Abstract base class for any piece of monitored hardware."""

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
        """Validates a parsed JSONL record and builds this class from it.
        Called on HardwareComponent itself, it hands the record to the
        Factory (factory.create_component), which picks Camera/Microphone
        from record["component_type"]. Raises ValueError on a missing field,
        unknown type or bad value."""
        if cls is HardwareComponent:
            from . import factory  # imported here: factory imports this module
            return factory.create_component(record)
        _require_fields(record, cls.REQUIRED_FIELDS, "Component")

        component_type = str(record["component_type"])
        component_type = component_type.strip().lower()
        if component_type != cls.__name__.lower():
            raise ValueError(f"{cls.__name__}.from_dict() got a '{record['component_type']}' record.")

        installed_on = record.get("installed_on")
        if installed_on is not None and not isinstance(installed_on, str):
            raise ValueError(f"Component '{record['component_id']}': installed_on must be a date "
                             f"string like '2021-05-30', got {installed_on!r}.")
        cls._validate_record(record)
        try:
            # cls is always a subclass here (the base class went to the factory above), whose __init__
            # takes its RECORD_FIELDS; PyCharm checks the call against HardwareComponent.__init__
            # noinspection PyArgumentList
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
        """Years since installation, or None if the installation date is unknown."""
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

    @abstractmethod
    def run_diagnostics(self):
        """Overridden by subclasses. Returns True if the component is healthy."""
        raise NotImplementedError("Subclasses must implement run_diagnostics().")

    @abstractmethod
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
        """Adds a component that belongs to this workstation. Raises
        ValueError for a component of another workstation or a reused id,
        so the workstation never holds two components with the same id."""
        if component.workstation_id != self.workstation_id:
            raise ValueError(f"Component '{component.component_id}' belongs to workstation "
                             f"'{component.workstation_id}', not '{self.workstation_id}'.")
        if self.find_component(component.component_id) is not None:
            raise ValueError(f"Workstation '{self.workstation_id}' already has a component "
                             f"with id '{component.component_id}'.")
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
    """A technician. There are no specialists: every technician can take any
    call (see maintenance.py for who gets which)."""

    REQUIRED_FIELDS = ("tech_id", "name")

    def __init__(self, tech_id: str, name: str):
        self.tech_id = tech_id
        self.name = name
        self.assigned_requests = []  # רשימת בקשות התחזוקה המשויכות לטכנאי

    @classmethod
    def from_dict(cls, record):
        """Factory: builds a Technician from a parsed JSONL record. Raises
        ValueError if a required field is missing or blank. Other fields
        (the specialty and workstation list older files still have) are
        ignored."""
        _require_fields(record, cls.REQUIRED_FIELDS, "Technician")
        return cls(tech_id=record["tech_id"], name=record["name"])

    def __repr__(self):
        return f"Technician(id='{self.tech_id}', name='{self.name}')"

    def __str__(self):
        open_count = sum(1 for r in self.assigned_requests if r.status != "RESOLVED")
        return f"טכנאי: {self.name} | משימות פתוחות: {open_count}"


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
        self.opened_at: datetime | None = opened_at or datetime.now()
        self.resolved_at: datetime | None = None
        self._status = "OPEN"
        self.assigned_tech_id: str | None = None
        self.assigned_tech_name: str | None = None  # as saved in the log, for a technician who has since left
        # Gemini's triage (api_client.Triage), once there is one: see services.apply_triage
        self.cause: str | None = None
        self.estimated_minutes: int | None = None

    # every field of a maintenance-log record, in the order of the CSV report's columns:
    # to_dict() writes them, from_dict() reads them, the CSV export lists them
    FIELDS = ("request_id", "status", "priority", "error_type", "workstation_id", "component_id",
              "assigned_tech_id", "assigned_tech_name", "opened_at", "resolved_at", "alert_id",
              "cause", "estimated_minutes")
    NUMBER_FIELDS = ("alert_id", "priority", "estimated_minutes")
    TIME_FIELDS = ("opened_at", "resolved_at")  # datetimes here, ISO text in a record
    # the rest are text when present (request_id must also be filled in)
    TEXT_FIELDS = tuple(sorted(set(FIELDS) - set(NUMBER_FIELDS), key=FIELDS.index))

    @classmethod
    def from_dict(cls, record):
        """Factory: builds a MaintenanceRequest from one maintenance-log record
        (a call's state as the desk saved it), each field with its type: the
        times become datetimes. A field left out gets its default; an unknown
        time stays None rather than "now". Raises ValueError naming a field
        that is missing (request_id), of the wrong type, or impossible (an
        unknown status, a time that isn't an ISO date)."""
        kind = "MaintenanceRequest"
        _require_fields(record, ("request_id",), kind)
        for field in cls.NUMBER_FIELDS:
            if record.get(field) is not None:
                _check_number(record, field, kind, integer=True)
        for field in cls.TEXT_FIELDS:
            value = record.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{kind} field '{field}' must be text, got {value!r}.")
        priority = record.get("priority")
        request = cls(record["request_id"], record.get("alert_id"), 5 if priority is None else priority,
                      error_type=record.get("error_type") or "", workstation_id=record.get("workstation_id") or "",
                      component_id=record.get("component_id") or "")
        for field in cls.TIME_FIELDS:
            setattr(request, field, _parse_time(record, field, kind))
        status = record.get("status") or "OPEN"
        try:
            request.status = status  # the status setter's own check
        except ValueError:
            raise ValueError(f"{kind} field 'status' must be one of {sorted(cls.VALID_STATUSES)}, "
                             f"got {status!r}.") from None
        request.assigned_tech_id = record.get("assigned_tech_id")
        request.assigned_tech_name = record.get("assigned_tech_name")
        request.cause = record.get("cause")
        request.estimated_minutes = record.get("estimated_minutes")
        return request

    @property
    def priority(self) -> int:
        return self._priority

    @priority.setter
    def priority(self, value: int):
        # bool is an int in Python, but True is not a priority
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
            raise ValueError(f"MaintenanceRequest priority must be a whole number from 1 (most urgent) "
                             f"to 5, got {value!r}.")
        self._priority = value

    @property
    def status(self) -> str:
        return self._status

    @status.setter
    def status(self, new_status: str):
        new_status = str(new_status).upper()
        if new_status not in self.VALID_STATUSES:
            raise ValueError(f"סטטוס לא חוקי: {new_status}. סטטוסים מותרים: {self.VALID_STATUSES}")
        self._check_not_resolved(new_status)
        self._status = new_status

    def _check_not_resolved(self, new_status):
        """A resolved call is closed for good: a fault that comes back opens a new call."""
        if getattr(self, "_status", None) == "RESOLVED" and new_status != "RESOLVED":
            raise ValueError(f"Maintenance call {self.request_id} is already resolved; "
                             f"it can't go back to {new_status}.")

    def assign_to(self, tech_id: str):
        self._check_not_resolved("IN_PROGRESS")  # before anything changes
        self.assigned_tech_id = tech_id
        self.status = "IN_PROGRESS"

    def resolve(self):
        self.status = "RESOLVED"
        self.resolved_at = datetime.now()

    def to_dict(self) -> dict:
        """The call as a log record: every field in FIELDS, the times as ISO text."""
        record = {field: getattr(self, field) for field in self.FIELDS}
        for field in self.TIME_FIELDS:
            record[field] = record[field].isoformat() if record[field] else None
        return record

    def __repr__(self):
        return f"MaintenanceRequest(id='{self.request_id}', alert_id={self.alert_id}, status='{self.status}')"

    def __str__(self):
        return f"בקשת תחזוקה #{self.request_id} [עדיפות: {self.priority}] - סטטוס: {self.status}"
