import os
import sys
import types

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# The package __init__ imports the Tkinter dashboard. Some environments (CI
# images, containers) ship Python without Tk, so stub it; the tests never
# open a window.
try:
    import tkinter  # noqa: F401
except ImportError:
    sys.modules["tkinter"] = types.ModuleType("tkinter")

from hardware_monitor.repository import (  # noqa: E402
    load_workstations_from_jsonl,
    load_technicians_from_jsonl,
)

SAMPLE_DATA_PATH = os.path.join(PROJECT_ROOT, "data", "sample_data.jsonl")
TECHNICIANS_PATH = os.path.join(PROJECT_ROOT, "data", "technicians.jsonl")


@pytest.fixture
def workstations():
    return load_workstations_from_jsonl(SAMPLE_DATA_PATH)


@pytest.fixture
def technicians():
    return load_technicians_from_jsonl(TECHNICIANS_PATH)


@pytest.fixture(autouse=True)
def isolated_alert_log(tmp_path, monkeypatch):
    """Keeps AlertManager from appending to the real logs/ folder."""
    monkeypatch.setattr("hardware_monitor.processing.LOG_PATH", str(tmp_path / "hardware_history.jsonl"))
