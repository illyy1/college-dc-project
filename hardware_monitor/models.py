"""OOP core: HardwareComponent hierarchy (inheritance/polymorphism) and the
Workstation class, which composes components (composition)."""


class HardwareComponent:
    """Base class for any piece of monitored hardware."""

    ALLOWED_STATUSES = {"ACTIVE", "DISCONNECTED", "TESTING", "ERROR"}

    def __init__(self, component_id, name, workstation_id, connection_status="ACTIVE"):
        self.component_id = component_id
        self.name = name
        self.workstation_id = workstation_id
        self._connection_status = None
        self.connection_status = connection_status  # goes through the validating setter

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

    def run_diagnostics(self):
        """Overridden by subclasses. Returns True if the component is healthy."""
        raise NotImplementedError("Subclasses must implement run_diagnostics().")

    def __repr__(self):
        return f"<{self.component_type} id={self.component_id} status={self.connection_status}>"


class Camera(HardwareComponent):
    def __init__(self, component_id, name, workstation_id, connection_status="ACTIVE",
                 camera_index=0, resolution="1920x1080"):
        super().__init__(component_id, name, workstation_id, connection_status)
        self.camera_index = camera_index
        self.resolution = resolution

    def run_diagnostics(self):
        """Camera-specific health check: valid resolution and not disconnected."""
        try:
            width, height = (int(part) for part in self.resolution.lower().split("x"))
            resolution_ok = width > 0 and height > 0
        except (ValueError, AttributeError):
            resolution_ok = False

        healthy = resolution_ok and self.connection_status != "DISCONNECTED"
        self.connection_status = "ACTIVE" if healthy else "DISCONNECTED"
        return healthy


class Microphone(HardwareComponent):
    MIN_SAMPLE_RATE = 8000

    def __init__(self, component_id, name, workstation_id, connection_status="ACTIVE",
                 sample_rate=44100, sensitivity_db=-40):
        super().__init__(component_id, name, workstation_id, connection_status)
        self.sample_rate = sample_rate
        self.sensitivity_db = sensitivity_db

    def run_diagnostics(self):
        """Microphone-specific health check: sane sample rate and not disconnected."""
        rate_ok = isinstance(self.sample_rate, (int, float)) and self.sample_rate >= self.MIN_SAMPLE_RATE
        healthy = rate_ok and self.connection_status != "DISCONNECTED"
        self.connection_status = "ACTIVE" if healthy else "DISCONNECTED"
        return healthy


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

    def __repr__(self):
        return f"<Workstation {self.workstation_id} '{self.name}' components={len(self._components)}>"
