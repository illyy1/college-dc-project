"""Stand-ins for the dashboard's other external dependencies: the clock, its
background threads, and the microphone and camera signals. Used by the
`dash` fixture in conftest.py (Tk is faked in fake_tk.py)."""


class FakeClock:
    """Stands in for the time module. sleep() moves time forward, lets the
    fake main loop run, and stops a monitoring loop after stop_after sleeps."""

    def __init__(self):
        self.now, self.app, self.sleeps, self.stop_after = 1000.0, None, 0, None

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.app.root.flush()
        self.sleeps += 1
        if self.stop_after is not None and self.sleeps >= self.stop_after:
            self.app.running = False

    def advance(self, seconds):
        self.now += seconds


def run_now(task):
    """Stands in for the dashboard's start_thread: the task runs inline, at once."""
    task()


class ScriptedMic:
    """Returns the scripted values in order, then keeps repeating the last one."""

    def __init__(self):
        self.connected, self.volumes, self.calls, self.forced = [True], [0.2], 0, 0

    @staticmethod
    def _next(script):
        return script.pop(0) if len(script) > 1 else script[0]

    def is_connected(self):
        self.calls += 1
        return self._next(self.connected)

    def read_volume(self):
        return self._next(self.volumes)

    def force_fault(self):
        self.forced += 1


class ScriptedCamera(ScriptedMic):
    def __init__(self):
        super().__init__()
        self.frames_ok = [True]

    def read_frame_ok(self):
        return self._next(self.frames_ok)
