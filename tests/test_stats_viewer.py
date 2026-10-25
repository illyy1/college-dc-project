"""TDD specification for hardware_monitor/stats_viewer.py (not written yet):
the standalone live statistics window, started with `python main.py --stats`.

What the module must provide:
- format_summary(stats), format_alert_types(stats), format_technicians(stats),
  format_recent(stats): the text of each section. Columns are separated by
  at least two spaces; counts read "1 call" / "2 calls".
- StatsViewer(log_path, technicians_path, refresh_seconds=2)
    builds its window with `tkinter` (imported in the module as `tk`), shows
    the statistics at once, and re-reads the log every refresh_seconds.
    .displayed() -> {"summary", "alert_types", "technicians", "recent"}: the
                    text currently on screen
    .refresh()   -> re-read now, update the window, schedule the next refresh
    .run()       -> enter the window's main loop
    .close()     -> stop refreshing and close the window
    .log_path, .technicians_path, .root -> what it was opened on, and its window
- run_stats_viewer(log_path=MAINTENANCE_LOG_PATH, technicians_path=TECHNICIANS_PATH)
    opens the viewer on the real files, runs it, and returns it once closed.
- main.py: `--stats` starts only the viewer. Like the dashboard, main.py
  imports it inside main(), so the other modes work without tkinter.

The tests replace tkinter with a fake, so no window is opened.
"""

import json
import re
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

import main
from hardware_monitor import stats_viewer
from hardware_monitor.config import MAINTENANCE_LOG_PATH, TECHNICIANS_PATH
from hardware_monitor.stats import compute_stats
from hardware_monitor.stats_viewer import (
    StatsViewer,
    format_alert_types,
    format_recent,
    format_summary,
    format_technicians,
)

BAR = "█"


def columns(line):
    """A formatted line split into its columns (separated by two or more spaces)."""
    return re.split(r"\s{2,}", line.strip())


# --- test data ---

@pytest.fixture
def make_call():
    names = {"T1": "Dana Levi", "T2": "Yossi Cohen", "T3": "Noa Mizrahi", "T4": "Avi Peretz"}

    def build(number, error_type, tech_id, status, opened_at="09:00", workstation_id="WS-104"):
        return {"request_id": f"MR-{number:04d}", "error_type": error_type, "workstation_id": workstation_id,
                "status": status, "assigned_tech_id": tech_id, "assigned_tech_name": names.get(tech_id),
                "opened_at": f"2026-10-20T{opened_at}:00"}

    return build


@pytest.fixture
def history(make_call):
    """Dana 3 calls (1 open), Yossi 2 (1 open), Noa 1 (open), Avi 1 (resolved)."""
    return [
        make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED", "09:00"),
        make_call(2, "CAMERA_FAILURE", "T3", "IN_PROGRESS", "09:10", workstation_id="WS-107"),
        make_call(3, "MICROPHONE_DISCONNECTED", "T2", "RESOLVED", "09:20", workstation_id="WS-105"),
        make_call(4, "MICROPHONE_SILENCE", "T2", "IN_PROGRESS", "09:30", workstation_id="WS-105"),
        make_call(5, "CAMERA_FAILURE", "T1", "RESOLVED", "09:40"),
        make_call(6, "MONITOR_OFF", "T4", "RESOLVED", "09:50", workstation_id="WS-101"),
        make_call(7, "CAMERA_FAILURE", "T1", "IN_PROGRESS", "10:00"),
    ]


@pytest.fixture
def stats(history, technicians):
    return compute_stats(history, technicians)


# --- the text of each section ---

def test_summary(stats):
    assert format_summary(stats) == "7 calls: 3 open, 4 resolved"


def test_summary_with_no_calls(technicians):
    assert format_summary(compute_stats([], technicians)) == "No maintenance calls yet."


def test_alert_types_section(stats):
    assert [columns(line) for line in format_alert_types(stats).splitlines()] == [
        ["Camera", "4 calls", "2 open", "2 resolved"],
        ["Microphone", "2 calls", "1 open", "1 resolved"],
        ["Other", "1 call", "0 open", "1 resolved"],
    ]


def test_alert_types_section_with_no_calls(technicians):
    assert format_alert_types(compute_stats([], technicians)) == "No alerts yet."


def test_technician_spread_section(stats):
    lines = format_technicians(stats).splitlines()

    assert [columns(line)[:5] for line in lines] == [
        ["Dana Levi", "3 calls", "1 open", "2 resolved", "42.9%"],
        ["Yossi Cohen", "2 calls", "1 open", "1 resolved", "28.6%"],
        ["Avi Peretz", "1 call", "0 open", "1 resolved", "14.3%"],
        ["Noa Mizrahi", "1 call", "1 open", "0 resolved", "14.3%"],
    ]


def test_the_spread_bar_is_scaled_to_the_busiest_technician(stats):
    bars = [columns(line)[5] for line in format_technicians(stats).splitlines()]
    assert [len(bar) for bar in bars] == [20, 13, 7, 7]  # 3, 2, 1 and 1 calls; 20 blocks for the busiest
    assert all(set(bar) == {BAR} for bar in bars)


def test_an_idle_technician_has_no_bar(make_call, technicians):
    stats = compute_stats([make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED")], technicians)
    idle = format_technicians(stats).splitlines()[1]
    assert columns(idle) == ["Avi Peretz", "0 calls", "0 open", "0 resolved", "0.0%"]


def test_the_spread_lines_up_in_columns(stats):
    lines = format_technicians(stats).splitlines()
    assert len({line.index(" call") for line in lines}) == 1  # the counts line up
    assert len({line.index("%") for line in lines}) == 1


def test_recent_calls_section_shows_who_took_each_alert(stats):
    lines = format_recent(stats).splitlines()
    assert columns(lines[0]) == ["MR-0007", "Camera", "WS-104", "Dana Levi", "IN_PROGRESS"]
    assert columns(lines[1]) == ["MR-0006", "Other", "WS-101", "Avi Peretz", "RESOLVED"]
    assert len(lines) == 7


def test_recent_calls_section_with_no_calls(technicians):
    assert format_recent(compute_stats([], technicians)) == "No calls yet."


# --- the live window ---

class FakeWidget:
    def __init__(self, *args, **options):
        self.options = dict(options)

    def pack(self, **kwargs):
        pass

    def grid(self, **kwargs):
        pass

    def config(self, **options):
        self.options.update(options)

    configure = config


class FakeTk(FakeWidget):
    """Records scheduled callbacks instead of running a real event loop."""

    def __init__(self):
        super().__init__()
        self.scheduled, self.window_title, self.destroyed, self.loop_started = [], None, False, False

    def title(self, text):
        self.window_title = text

    def geometry(self, spec):
        pass

    def protocol(self, name, handler):
        self.close_handler = handler

    def after(self, delay_ms, callback):
        self.scheduled.append((delay_ms, callback))
        return f"after#{len(self.scheduled)}"

    def after_cancel(self, ident):
        pass

    def mainloop(self):
        self.loop_started = True

    def destroy(self):
        self.destroyed = True

    def run_next(self):
        """Runs the most recently scheduled callback, like Tk's loop would."""
        _, callback = self.scheduled.pop()
        callback()


FAKE_TK = SimpleNamespace(Tk=FakeTk, Label=FakeWidget, Frame=FakeWidget, LabelFrame=FakeWidget,
                          Text=FakeWidget, StringVar=None, TclError=RuntimeError)


@pytest.fixture
def log(tmp_path, history):
    """A temporary maintenance log; .append(record) adds a line like the dashboard does."""
    path = tmp_path / "maintenance_requests.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in history), encoding="utf-8")

    def append(record):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    return SimpleNamespace(path=str(path), append=append)


@pytest.fixture
def open_viewer(monkeypatch, log):
    monkeypatch.setattr(stats_viewer, "tk", FAKE_TK)

    def build(log_path=log.path, **options):
        return StatsViewer(log_path, TECHNICIANS_PATH, **options)

    return build


def test_the_window_shows_the_statistics_at_once(open_viewer, stats):
    viewer = open_viewer()

    assert viewer.root.window_title == "Live Alert Statistics"
    assert viewer.displayed() == {
        "summary": format_summary(stats),
        "alert_types": format_alert_types(stats),
        "technicians": format_technicians(stats),
        "recent": format_recent(stats),
    }


def test_the_window_refreshes_itself_every_two_seconds(open_viewer):
    viewer = open_viewer()
    assert [delay for delay, _ in viewer.root.scheduled] == [2000]


def test_the_refresh_interval_can_be_changed(open_viewer):
    assert open_viewer(refresh_seconds=5).root.scheduled[-1][0] == 5000


def test_a_new_call_appears_on_the_next_refresh(open_viewer, log, make_call):
    viewer = open_viewer()
    log.append(make_call(8, "MICROPHONE_SILENCE", "T2", "IN_PROGRESS", "10:10", workstation_id="WS-105"))
    assert viewer.displayed()["summary"] == "7 calls: 3 open, 4 resolved"  # not before the refresh

    viewer.root.run_next()

    assert viewer.displayed()["summary"] == "8 calls: 4 open, 4 resolved"
    assert columns(viewer.displayed()["recent"].splitlines()[0]) == \
        ["MR-0008", "Microphone", "WS-105", "Yossi Cohen", "IN_PROGRESS"]


def test_a_resolved_call_is_updated_on_the_next_refresh(open_viewer, log, make_call):
    viewer = open_viewer()
    log.append(make_call(7, "CAMERA_FAILURE", "T1", "RESOLVED", "10:00"))
    viewer.root.run_next()
    assert viewer.displayed()["summary"] == "7 calls: 2 open, 5 resolved"


def test_each_refresh_schedules_exactly_one_more(open_viewer):
    viewer = open_viewer()
    for _ in range(5):
        viewer.root.run_next()
        assert len(viewer.root.scheduled) == 1  # never piles up refreshes


def test_the_window_starts_empty_before_the_first_call(open_viewer, tmp_path):
    viewer = open_viewer(log_path=str(tmp_path / "no_log_yet.jsonl"))
    assert viewer.displayed()["summary"] == "No maintenance calls yet."
    assert len(viewer.displayed()["technicians"].splitlines()) == 4  # everyone listed, at 0


def test_an_unreadable_log_keeps_the_last_numbers_and_says_so(open_viewer, log, monkeypatch):
    viewer = open_viewer()
    before = viewer.displayed()

    with mock.patch("hardware_monitor.repository.open", create=True,
                    side_effect=PermissionError(13, "Permission denied")):
        viewer.root.run_next()  # must not crash the window

    shown = viewer.displayed()
    assert shown["technicians"] == before["technicians"] and shown["recent"] == before["recent"]
    assert shown["summary"].startswith(before["summary"])
    assert "Could not read the maintenance log" in shown["summary"]
    assert len(viewer.root.scheduled) == 1  # and it keeps trying

    viewer.root.run_next()  # readable again
    assert viewer.displayed() == before


def test_closing_stops_the_refreshes(open_viewer):
    viewer = open_viewer()
    viewer.close()
    assert viewer.root.destroyed

    _, pending = viewer.root.scheduled.pop()
    pending()  # a refresh that was already queued when the window closed
    assert viewer.root.scheduled == []


def test_the_close_button_closes_the_viewer(open_viewer):
    viewer = open_viewer()
    viewer.root.close_handler()
    assert viewer.root.destroyed


def test_run_enters_the_window_loop(open_viewer):
    viewer = open_viewer()
    viewer.run()
    assert viewer.root.loop_started


# --- starting it: `python main.py --stats` ---

def test_run_stats_viewer_opens_the_real_files_by_default(monkeypatch):
    monkeypatch.setattr(stats_viewer, "tk", FAKE_TK)
    viewer = stats_viewer.run_stats_viewer()  # only reads the real files

    assert (viewer.log_path, viewer.technicians_path) == (MAINTENANCE_LOG_PATH, TECHNICIANS_PATH)
    assert viewer.root.loop_started


def test_main_stats_flag_starts_only_the_viewer(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main.py", "--stats"])
    monkeypatch.setattr(stats_viewer, "tk", FAKE_TK)  # never a real window, even if the wiring is wrong
    with mock.patch("hardware_monitor.stats_viewer.run_stats_viewer") as viewer, \
            mock.patch.object(main, "run_demo") as demo, \
            mock.patch.object(main, "publish_maintenance_reports") as reports:
        main.main()

    viewer.assert_called_once_with()
    demo.assert_not_called()
    reports.assert_not_called()


def test_the_stats_flag_is_listed_in_the_help(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main.py", "--help"])
    with pytest.raises(SystemExit):
        main.main()
    assert "--stats" in capsys.readouterr().out
