"""Resource management: a custom context manager guarding diagnostic sessions."""


class HardwareDiagnosticSession:
    """Locks a component's status to TESTING for the duration of a diagnostic
    check and safely restores it afterwards — even if the check raises."""

    def __init__(self, component):
        self.component = component
        self._previous_status = None

    def __enter__(self):
        self._previous_status = self.component.connection_status
        self.component.connection_status = "TESTING"
        return self.component

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            print(f"Diagnostic session for {self.component.component_id} raised {exc_type.__name__}: {exc_value}")
            self.component.connection_status = "ERROR"
        else:
            self.component.connection_status = self._previous_status
        return True  # exception (if any) was handled safely, don't propagate
