"""Resource management: a custom context manager guarding diagnostic sessions."""


class DiagnosticConnection:
    """Opens a simulated diagnostic port to a component for the duration of a
    hardware test. On entry it claims a free port and locks the component to
    TESTING; on exit it always releases the port and leaves the component in
    a real status — even if the test raises.

    Satisfies the context manager requirements (__enter__, __exit__,
    exception safety)."""

    PORT_POOL = ("DIAG-1", "DIAG-2", "DIAG-3")
    _ports_in_use = set()

    def __init__(self, component, verbose=True):
        self.component = component
        self.port = None
        self.verbose = verbose  # False hides the open/close messages; failures are always reported

    @classmethod
    def available_ports(cls):
        return [p for p in cls.PORT_POOL if p not in cls._ports_in_use]

    def __enter__(self):
        free = self.available_ports()
        if not free:
            raise RuntimeError(f"No free diagnostic port for {self.component.component_id}.")
        self.port = free[0]
        DiagnosticConnection._ports_in_use.add(self.port)
        if self.verbose:
            print(f"[DIAGNOSTICS] Opening diagnostic channel {self.port} for component "
                  f"'{self.component.component_id}'...")
        try:
            self.component.begin_test()
        except Exception:
            self._release_port()
            raise
        return self.component

    def __exit__(self, exc_type, exc_value, traceback):
        if self.verbose:
            print(f"[DIAGNOSTICS] Closing diagnostic channel {self.port} for component "
                  f"'{self.component.component_id}'.")
        try:
            if exc_type is not None:
                print(f"[DIAGNOSTICS] Session failed with error ({exc_type.__name__}): {exc_value}")
                self.component.end_test(final_status="ERROR")
            else:
                self.component.end_test()
        finally:
            self._release_port()
        return False  # let the caller see the exception; cleanup is already done

    def _release_port(self):
        DiagnosticConnection._ports_in_use.discard(self.port)
        self.port = None


# earlier name, kept so existing code using it keeps working
HardwareDiagnosticSession = DiagnosticConnection
