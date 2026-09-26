"""Synthetic mic/camera signal generators.

These stand in for real hardware so the alert pipeline (silence detection,
disconnect detection, GitHub alerting, the Tkinter UI) can be exercised and
tested on demand, without needing a physical microphone or camera attached.

Each fault is an episode with separate chances per check of starting and of
clearing, so the simulated room is busy — frequent faults — but each fault
clears within seconds instead of lingering. The mic is checked every second
and the camera every 3 seconds; with the defaults below the dashboard shows
a new popup roughly every 15-20 seconds on average.
"""

import random

from .config import SILENCE_THRESHOLD

_LOUD_VOLUME_RANGE = (0.05, 0.25)
_SILENT_VOLUME_RANGE = (0.0, SILENCE_THRESHOLD * 0.5)


def _next_fault_state(in_fault, onset_chance, recovery_chance):
    """One check of a fault episode: start it with onset_chance, or end an
    ongoing one with recovery_chance."""
    if in_fault:
        return random.random() >= recovery_chance
    return random.random() < onset_chance


class SyntheticMicrophoneSource:
    """Simulates a microphone that mostly reports normal volume levels, but
    frequently drops into a short disconnect or a silent patch."""

    def __init__(self, disconnect_chance=0.035, reconnect_chance=0.2,
                 silence_chance=0.025, sound_return_chance=0.06):
        self.disconnect_chance = disconnect_chance
        self.reconnect_chance = reconnect_chance
        self.silence_chance = silence_chance
        self.sound_return_chance = sound_return_chance
        self._disconnected = False
        self._silent = False

    def is_connected(self):
        self._disconnected = _next_fault_state(self._disconnected, self.disconnect_chance, self.reconnect_chance)
        return not self._disconnected

    def read_volume(self):
        self._silent = _next_fault_state(self._silent, self.silence_chance, self.sound_return_chance)
        low, high = _SILENT_VOLUME_RANGE if self._silent else _LOUD_VOLUME_RANGE
        return random.uniform(low, high)


class SyntheticCameraSource:
    """Simulates a camera that mostly stays connected and healthy, but
    frequently has a short disconnect or a run of frames that fail to read."""

    def __init__(self, disconnect_chance=0.08, reconnect_chance=0.35,
                 frame_failure_chance=0.05, frame_recovery_chance=0.4):
        self.disconnect_chance = disconnect_chance
        self.reconnect_chance = reconnect_chance
        self.frame_failure_chance = frame_failure_chance
        self.frame_recovery_chance = frame_recovery_chance
        self._disconnected = False
        self._frames_failing = False

    def is_connected(self):
        self._disconnected = _next_fault_state(self._disconnected, self.disconnect_chance, self.reconnect_chance)
        return not self._disconnected

    def read_frame_ok(self):
        """Only meaningful while connected: simulates a run of frames that
        fail to read even though the device reports connected."""
        self._frames_failing = _next_fault_state(
            self._frames_failing, self.frame_failure_chance, self.frame_recovery_chance)
        return not self._frames_failing
