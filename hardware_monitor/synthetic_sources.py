"""Synthetic mic/camera signal generators.

These stand in for real hardware so the alert pipeline (silence detection,
disconnect detection, GitHub alerting, the Tkinter UI) can be exercised and
tested on demand, without needing a physical microphone or camera attached.
"""

import random

from .config import SILENCE_THRESHOLD

_LOUD_VOLUME_RANGE = (0.05, 0.25)
_SILENT_VOLUME_RANGE = (0.0, SILENCE_THRESHOLD * 0.5)


class SyntheticMicrophoneSource:
    """Simulates a microphone that mostly reports normal volume levels, but
    periodically drifts into a silent patch or a full disconnect so the
    silence/disconnect alert paths can be tested."""

    def __init__(self, silence_chance=0.15, disconnect_chance=0.03):
        self.silence_chance = silence_chance
        self.disconnect_chance = disconnect_chance
        self._connected = True
        self._silent = False

    def is_connected(self):
        if random.random() < self.disconnect_chance:
            self._connected = not self._connected
        return self._connected

    def read_volume(self):
        if random.random() < self.silence_chance:
            self._silent = not self._silent
        low, high = _SILENT_VOLUME_RANGE if self._silent else _LOUD_VOLUME_RANGE
        return random.uniform(low, high)


class SyntheticCameraSource:
    """Simulates a camera that mostly stays connected and healthy, but
    periodically flips to disconnected or a frame-read failure."""

    def __init__(self, disconnect_chance=0.08, frame_failure_chance=0.05):
        self.disconnect_chance = disconnect_chance
        self.frame_failure_chance = frame_failure_chance
        self._connected = True

    def is_connected(self):
        if random.random() < self.disconnect_chance:
            self._connected = not self._connected
        return self._connected

    def read_frame_ok(self):
        """Only meaningful while connected: occasionally simulate a frame
        that fails to read even though the device reports connected."""
        return random.random() >= self.frame_failure_chance
