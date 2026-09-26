"""Resource management: a custom context manager guarding diagnostic sessions."""


class HardwareDiagnosticSession:
    """Simulates locking a hardware diagnostic channel/port and updating component status safely.

    Satisfies context manager requirements (__enter__, __exit__, exception
    safety).
    """

    def __init__(self, component):
        self.component = component
        self._previous_status = None

    def __enter__(self):
        print(
            f"[DIAGNOSTICS] Opening diagnostic channel for component '{self.component.component_id}'..."
        )
        self._previous_status = self.component.connection_status
        self.component.connection_status = "TESTING"
        return self.component

    def __exit__(self, exc_type, exc_value, traceback):
        print(
            f"[DIAGNOSTICS] Closing diagnostic channel for component '{self.component.component_id}'."
        )

        if exc_type is not None:
            print(
                f"[DIAGNOSTICS] Session failed with error ({exc_type.__name__}): {exc_value}"
            )
            self.component.connection_status = "ERROR"
            return False

        if self.component.connection_status == "TESTING":
            self.component.connection_status = "ACTIVE"

        return False
