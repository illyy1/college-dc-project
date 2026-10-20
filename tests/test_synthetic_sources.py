"""The simulated microphone and camera the dashboard monitors: how a fault
episode starts and clears, forced faults, the volume ranges, and that the
default settings give a busy room whose faults clear on their own."""

import random

import pytest

from hardware_monitor import synthetic_sources
from hardware_monitor.config import SILENCE_THRESHOLD
from hardware_monitor.synthetic_sources import SyntheticCameraSource, SyntheticMicrophoneSource, _next_fault_state


@pytest.fixture
def rolls(monkeypatch):
    """Makes random.random() return the given values in order."""
    values = []
    monkeypatch.setattr(synthetic_sources.random, "random", lambda: values.pop(0))
    return values


# --- one check of a fault episode ---

@pytest.mark.parametrize("roll, starts", [(0.29, True), (0.3, False), (0.31, False)])
def test_a_fault_starts_when_the_roll_is_below_the_onset_chance(rolls, roll, starts):
    rolls.append(roll)
    assert _next_fault_state(False, onset_chance=0.3, recovery_chance=0.5) is starts


@pytest.mark.parametrize("roll, still_faulty", [(0.49, False), (0.5, True), (0.51, True)])
def test_a_fault_clears_when_the_roll_is_below_the_recovery_chance(rolls, roll, still_faulty):
    rolls.append(roll)
    assert _next_fault_state(True, onset_chance=0.3, recovery_chance=0.5) is still_faulty


def test_chance_zero_never_and_chance_one_always():
    for _ in range(1000):
        assert _next_fault_state(False, 0.0, 0.0) is False   # never starts
        assert _next_fault_state(True, 0.0, 0.0) is True      # never clears
        assert _next_fault_state(False, 1.0, 1.0) is True     # always starts
        assert _next_fault_state(True, 1.0, 1.0) is False     # always clears


# --- microphone ---

def test_a_healthy_microphone_stays_connected_and_loud():
    mic = SyntheticMicrophoneSource(disconnect_chance=0, silence_chance=0)
    for _ in range(500):
        assert mic.is_connected()
        assert 0.05 <= mic.read_volume() <= 0.25


def test_a_silent_patch_reads_below_the_silence_threshold():
    mic = SyntheticMicrophoneSource(silence_chance=1, sound_return_chance=0)
    assert all(0 <= mic.read_volume() < SILENCE_THRESHOLD for _ in range(500))


def test_a_disconnect_lasts_until_it_recovers(rolls):
    mic = SyntheticMicrophoneSource(disconnect_chance=0.5, reconnect_chance=0.5)
    rolls.extend([0.1, 0.9, 0.9, 0.1])  # starts, stays, stays, recovers
    assert [mic.is_connected() for _ in range(4)] == [False, False, False, True]


def test_a_forced_mic_fault_is_always_seen_by_the_next_check():
    mic = SyntheticMicrophoneSource(disconnect_chance=0, reconnect_chance=1)
    mic.force_fault()
    assert mic.is_connected() is False  # even though a disconnect "never" starts
    assert mic.is_connected() is True   # then it clears like any other


# --- camera ---

def test_a_healthy_camera_stays_connected_with_good_frames():
    camera = SyntheticCameraSource(disconnect_chance=0, frame_failure_chance=0)
    assert all(camera.is_connected() and camera.read_frame_ok() for _ in range(500))


@pytest.mark.parametrize("roll, connected, frame_ok", [
    (0.2, False, True),   # below 0.5: the forced fault is a disconnect
    (0.7, True, False),   # otherwise: frames fail to read
])
def test_a_forced_camera_fault_is_always_seen_by_the_next_check(monkeypatch, roll, connected, frame_ok):
    camera = SyntheticCameraSource(disconnect_chance=0, reconnect_chance=1,
                                   frame_failure_chance=0, frame_recovery_chance=1)
    with monkeypatch.context() as patched:
        patched.setattr(synthetic_sources.random, "random", lambda: roll)
        camera.force_fault()

    assert camera.is_connected() is connected
    if connected:
        assert camera.read_frame_ok() is frame_ok
    # and it clears on the following check
    assert camera.is_connected() is True and camera.read_frame_ok() is True


# --- the defaults give a busy room whose faults clear quickly ---

@pytest.mark.parametrize("make, check, onset, recovery", [
    (SyntheticMicrophoneSource, "is_connected", 0.035, 0.2),
    (SyntheticCameraSource, "is_connected", 0.08, 0.35),
])
def test_default_fault_rates(make, check, onset, recovery):
    random.seed(1234)
    source = make()
    checks = [getattr(source, check)() for _ in range(20000)]

    faulty_share = checks.count(False) / len(checks)
    expected = onset / (onset + recovery)  # long-run share of checks spent in a fault
    assert faulty_share == pytest.approx(expected, abs=0.03)

    longest, run = 0, 0
    for ok in checks:
        run = 0 if ok else run + 1
        longest = max(longest, run)
    assert 0 < longest < 60  # faults happen, and every one clears
