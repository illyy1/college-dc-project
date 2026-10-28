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
    .pies() -> {"technicians": [Slice], "alert_types": [Slice]}: the two pie
               charts currently drawn, each on its own tk.Canvas
               (.technician_pie_canvas and .alert_type_pie_canvas)
- pie_slices(parts) -> [Slice]: parts are (label, value) pairs, in order.
    Slice: label, value, percent, start, extent (degrees, as tk.Canvas
    create_arc takes them), color. Slices start at 90 degrees (12 o'clock)
    and follow each other counterclockwise, filling exactly 360; parts with
    no value get no slice; each slice in a pie gets a different color from
    PIE_COLORS.
- run_stats_viewer(log_path=MAINTENANCE_LOG_PATH, technicians_path=TECHNICIANS_PATH)
    opens the viewer on the real files, runs it, and returns it once closed.
- main.py: `--stats` starts only the viewer. Like the dashboard, main.py
  imports it inside main(), so the other modes work without tkinter.
- launch_in_background() -> the viewer's process (subprocess.Popen running
  `MAIN_SCRIPT --stats` with the same Python), or None if it could not start
- stop_background_viewer(process): closes it if it is still running (kills it
  if it doesn't close within 5 s); does nothing for None or an exited process
- main.py without flags opens the viewer next to the dashboard and closes it
  when the dashboard closes; `--no-stats` leaves it out.

The tests replace tkinter with a fake, so no window is opened.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest

import main
from hardware_monitor import stats_viewer
from hardware_monitor.config import MAINTENANCE_LOG_PATH, TECHNICIANS_PATH
from hardware_monitor.stats import compute_stats
from conftest import BAD_FIELDS, as_requests
from fake_tk import FAKE_TK, FakeCanvas, FakeTk
from hardware_monitor.stats_viewer import (
    BAR_WIDTH,
    PIE_COLORS,
    StatsViewer,
    format_alert_types,
    format_recent,
    format_summary,
    format_technicians,
    pie_slices,
)

BAR = "█"


def columns(line):
    """A formatted line split into its columns (separated by two or more spaces)."""
    return re.split(r"\s{2,}", line.strip())


# --- test data (make_call and history are in conftest.py) ---

@pytest.fixture
def stats(history, technicians):
    return compute_stats(as_requests(history), technicians)


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
    # 3, 2, 1 and 1 calls: BAR_WIDTH blocks for the busiest, the rest in proportion
    assert [len(bar) for bar in bars] == [BAR_WIDTH, round(BAR_WIDTH * 2 / 3), round(BAR_WIDTH / 3), round(BAR_WIDTH / 3)]
    assert all(set(bar) == {BAR} for bar in bars)


def test_an_idle_technician_has_no_bar(make_call, technicians):
    stats = compute_stats(as_requests([make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED")]), technicians)
    idle = format_technicians(stats).splitlines()[1]
    assert columns(idle) == ["Avi Peretz", "0 calls", "0 open", "0 resolved", "0.0%"]


def test_the_spread_with_no_technicians_at_all():
    # an empty technician file and no calls: nothing to scale the bars to
    assert format_technicians(compute_stats([], {})) == "No technicians."


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



@pytest.fixture
def log(write_jsonl, history):
    """A temporary maintenance log holding `history`; .append(record) adds a
    line like the dashboard does."""
    name = "maintenance_requests.jsonl"
    path = write_jsonl(name, *history)
    return SimpleNamespace(path=path, append=lambda record: write_jsonl(name, record, append=True))


@pytest.fixture
def open_viewer(monkeypatch, log, technicians_file):
    """Opens the viewer on a log and the test team's technician file."""
    monkeypatch.setattr("hardware_monitor.stats_viewer.tk", FAKE_TK)

    def build(log_path=log.path, **options):
        return StatsViewer(log_path, technicians_file, **options)

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


def test_the_viewer_defaults():
    # the one place the defaults are pinned; every other test reads them from stats_viewer
    assert (stats_viewer.REFRESH_SECONDS, BAR_WIDTH, stats_viewer.STOP_TIMEOUT_SECONDS) == (2, 20, 5)


def test_the_window_refreshes_itself_at_the_refresh_interval(open_viewer):
    viewer = open_viewer()
    assert [delay for delay, _ in viewer.root.scheduled] == [stats_viewer.REFRESH_SECONDS * 1000]


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


def test_the_window_starts_empty_before_the_first_call(open_viewer, tmp_path, technicians):
    viewer = open_viewer(log_path=str(tmp_path / "no_log_yet.jsonl"))
    assert viewer.displayed()["summary"] == "No maintenance calls yet."
    assert len(viewer.displayed()["technicians"].splitlines()) == len(technicians)  # everyone listed, at 0


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


def test_closing_still_works_if_cancelling_the_refresh_fails(open_viewer):
    viewer = open_viewer()
    root: FakeTk = viewer.root  # the fake window, which has after_cancel
    with mock.patch.object(root, "after_cancel", side_effect=FAKE_TK.TclError("already ran")):
        viewer.close()
    assert viewer.root.destroyed and not viewer.running


def test_the_close_button_closes_the_viewer(open_viewer):
    viewer = open_viewer()
    viewer.root.close_handler()
    assert viewer.root.destroyed


def test_run_enters_the_window_loop(open_viewer):
    viewer = open_viewer()
    viewer.run()
    assert viewer.root.loop_started


# --- pie charts: slicing ---

def test_slices_are_proportional_and_fill_the_circle():
    slices = pie_slices([("Dana Levi", 3), ("Yossi Cohen", 2), ("Avi Peretz", 1), ("Noa Mizrahi", 1)])

    assert [s.label for s in slices] == ["Dana Levi", "Yossi Cohen", "Avi Peretz", "Noa Mizrahi"]
    assert [s.value for s in slices] == [3, 2, 1, 1]
    assert [s.percent for s in slices] == pytest.approx([300 / 7, 200 / 7, 100 / 7, 100 / 7])
    assert [s.extent for s in slices] == pytest.approx([360 * 3 / 7, 360 * 2 / 7, 360 / 7, 360 / 7])
    assert sum(s.extent for s in slices) == pytest.approx(360)


def test_slices_start_at_twelve_o_clock_and_follow_each_other():
    slices = pie_slices([("a", 1), ("b", 1), ("c", 2)])

    assert slices[0].start == pytest.approx(90)
    for previous, current in zip(slices, slices[1:]):
        assert current.start == pytest.approx(previous.start + previous.extent)


@pytest.mark.parametrize("parts, labels", [
    ([("busy", 4), ("idle", 0)], ["busy"]),
    ([("idle", 0), ("busy", 4), ("also idle", 0)], ["busy"]),
    ([("a", 0), ("b", 0)], []),
    ([], []),
], ids=["idle-at-the-end", "idle-around", "all-idle", "nothing"])
def test_parts_without_a_value_get_no_slice(parts, labels):
    assert [s.label for s in pie_slices(parts)] == labels


def test_a_single_part_takes_the_whole_circle():
    (whole,) = pie_slices([("Camera", 5)])
    assert (whole.percent, whole.start, whole.extent) == (pytest.approx(100), pytest.approx(90), pytest.approx(360))


def test_every_slice_gets_its_own_color():
    slices = pie_slices([(f"tech {n}", 1) for n in range(len(PIE_COLORS))])
    assert [s.color for s in slices] == list(PIE_COLORS)


HEX_COLOR = re.compile(r"^#[0-9a-f]{6}$")


@pytest.mark.parametrize("count", [1, 8, 9, 12, 25, 50])
def test_colors_never_repeat_however_many_slices(count):
    # the staff can grow past any fixed palette: two technicians must never look alike
    colors = [s.color for s in pie_slices([(f"tech {n}", 1) for n in range(count)])]
    assert len(set(colors)) == count
    assert all(HEX_COLOR.match(color) for color in colors)  # a colour tk.Canvas accepts


# --- pie charts: in the window ---

def test_the_window_draws_the_technician_pie(open_viewer):
    viewer = open_viewer()
    pie = viewer.pies()["technicians"]

    assert [(s.label, round(s.percent, 1)) for s in pie] == \
        [("Dana Levi", 42.9), ("Yossi Cohen", 28.6), ("Avi Peretz", 14.3), ("Noa Mizrahi", 14.3)]
    arcs = viewer.technician_pie_canvas.of_kind("arc")
    assert [round(a.extent, 2) for a in arcs] == [round(s.extent, 2) for s in pie]
    assert [a.start for a in arcs] == pytest.approx([s.start for s in pie])
    assert [a.fill for a in arcs] == [s.color for s in pie]


def test_the_window_draws_the_alert_type_pie(open_viewer):
    viewer = open_viewer()
    pie = viewer.pies()["alert_types"]

    assert [(s.label, round(s.percent, 1)) for s in pie] == [("Camera", 57.1), ("Microphone", 28.6), ("Other", 14.3)]
    assert len(viewer.alert_type_pie_canvas.of_kind("arc")) == 3


def test_each_pie_has_a_legend_with_percentages(open_viewer):
    viewer = open_viewer()
    technician_legend = viewer.technician_pie_canvas.texts()
    type_legend = viewer.alert_type_pie_canvas.texts()

    for label, percent in [("Dana Levi", "42.9%"), ("Yossi Cohen", "28.6%"), ("Avi Peretz", "14.3%")]:
        assert any(label in text and percent in text for text in technician_legend)
    for label, percent in [("Camera", "57.1%"), ("Microphone", "28.6%"), ("Other", "14.3%")]:
        assert any(label in text and percent in text for text in type_legend)


def drawn_bottom(canvas):
    """The lowest point of anything drawn on the canvas."""
    return max(max(item.coords[1::2]) for item in canvas.items)


@pytest.mark.parametrize("entries", [1, 2, 9, 10, 25])
def test_the_legend_always_fits_on_the_canvas(entries):
    canvas = FakeCanvas(height=stats_viewer.PIE_DIAMETER + 2 * stats_viewer.PIE_MARGIN)
    stats_viewer.draw_pie(canvas, pie_slices([(f"tech {n}", 1) for n in range(entries)]))

    assert len(canvas.of_kind("text")) == entries  # one legend line per slice
    assert drawn_bottom(canvas) <= canvas.options["height"]  # nothing drawn below the canvas


def test_the_canvas_never_gets_shorter_than_the_pie():
    canvas = FakeCanvas(height=stats_viewer.PIE_DIAMETER + 2 * stats_viewer.PIE_MARGIN)
    stats_viewer.draw_pie(canvas, pie_slices([("only one", 1)]))
    assert canvas.options["height"] >= stats_viewer.PIE_DIAMETER + 2 * stats_viewer.PIE_MARGIN


def test_the_whole_team_fits_in_the_window(make_team, write_jsonl, make_call, monkeypatch):
    # the real staff can grow: a dozen technicians, all with calls, all in the legend
    team = make_team(12)
    technicians_path = write_jsonl("big_team.jsonl", *({"tech_id": t, "name": tech.name} for t, tech in team.items()))
    log_path = write_jsonl("big_log.jsonl", *(make_call(n, "CAMERA_FAILURE", f"T{n}", "RESOLVED")
                                               for n in range(1, 13)))
    monkeypatch.setattr("hardware_monitor.stats_viewer.tk", FAKE_TK)
    viewer = StatsViewer(log_path, technicians_path)

    canvas = viewer.technician_pie_canvas
    assert len(canvas.of_kind("text")) == 12
    assert drawn_bottom(canvas) <= canvas.options["height"]
    assert len({s.color for s in viewer.pies()["technicians"]}) == 12


def test_each_legend_entry_has_a_color_key_matching_its_slice(open_viewer):
    viewer = open_viewer()
    keys = [item.fill for item in viewer.technician_pie_canvas.of_kind("rectangle")]
    assert keys == [s.color for s in viewer.pies()["technicians"]]


def test_idle_technicians_are_left_out_of_the_pie(open_viewer, write_jsonl, make_call):
    viewer = open_viewer(log_path=write_jsonl("one_call.jsonl", make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED")))

    assert [s.label for s in viewer.pies()["technicians"]] == ["Dana Levi"]


def test_a_single_slice_is_drawn_as_a_full_circle(open_viewer, write_jsonl, make_call):
    # tk.Canvas draws nothing for a 360 degree arc, so a whole pie must be a circle
    viewer = open_viewer(log_path=write_jsonl("one_call.jsonl", make_call(1, "CAMERA_FAILURE", "T1", "RESOLVED")))

    canvas = viewer.alert_type_pie_canvas
    assert canvas.of_kind("arc") == []
    (circle,) = canvas.of_kind("oval")
    assert circle.fill == PIE_COLORS[0]


def test_pies_with_no_calls_say_so(open_viewer, tmp_path):
    viewer = open_viewer(log_path=str(tmp_path / "no_log_yet.jsonl"))

    assert viewer.pies() == {"technicians": [], "alert_types": []}
    for canvas in (viewer.technician_pie_canvas, viewer.alert_type_pie_canvas):
        assert canvas.of_kind("arc", "oval") == []
        assert "No calls yet" in canvas.texts()


def test_the_pies_follow_live_updates(open_viewer, log, make_call):
    viewer = open_viewer()
    log.append(make_call(8, "MICROPHONE_SILENCE", "T2", "IN_PROGRESS", "10:10"))
    log.append(make_call(9, "MICROPHONE_SILENCE", "T2", "IN_PROGRESS", "10:20"))
    viewer.root.run_next()

    # Yossi now has 4 calls and overtakes Dana's 3
    assert [(s.label, s.value) for s in viewer.pies()["technicians"]] == \
        [("Yossi Cohen", 4), ("Dana Levi", 3), ("Avi Peretz", 1), ("Noa Mizrahi", 1)]
    assert [(s.label, s.value) for s in viewer.pies()["alert_types"]] == \
        [("Camera", 4), ("Microphone", 4), ("Other", 1)]  # equal counts: alphabetical


def test_redrawing_replaces_the_old_pie(open_viewer):
    viewer = open_viewer()
    for _ in range(3):
        viewer.root.run_next()
    assert len(viewer.technician_pie_canvas.of_kind("arc")) == 4  # not 4 per refresh


def test_an_unreadable_log_leaves_the_pies_as_they_were(open_viewer):
    viewer = open_viewer()
    before = viewer.pies()
    with mock.patch("hardware_monitor.repository.open", create=True,
                    side_effect=PermissionError(13, "Permission denied")):
        viewer.root.run_next()
    assert viewer.pies() == before
    assert len(viewer.technician_pie_canvas.of_kind("arc")) == 4


# --- running alongside the dashboard ---
# The dashboard runs in another process and appends to the same log while
# the viewer re-reads it. The viewer only reads, on Tk's single thread, so
# it needs no lock; what it must survive is reading a line mid-write.

def test_a_half_written_call_appears_once_it_is_complete(open_viewer, log, make_call):
    viewer = open_viewer()
    line = json.dumps(make_call(8, "CAMERA_FAILURE", "T3", "IN_PROGRESS", "10:10")) + "\n"
    middle = len(line) // 2

    with open(log.path, "a", encoding="utf-8") as f:
        f.write(line[:middle])  # the dashboard is halfway through writing it
    viewer.root.run_next()
    assert viewer.displayed()["summary"] == "7 calls: 3 open, 4 resolved"  # not counted yet, and no crash

    with open(log.path, "a", encoding="utf-8") as f:
        f.write(line[middle:])
    viewer.root.run_next()
    assert viewer.displayed()["summary"] == "8 calls: 4 open, 4 resolved"


def test_refreshing_while_the_desk_keeps_writing(open_viewer, make_desk, alerts):
    # a real MaintenanceDesk in another thread stands in for the dashboard's process
    desk = make_desk()
    viewer = open_viewer(log_path=desk.log_path)
    done = threading.Event()

    def dashboard():
        for _ in range(30):
            request = desk.open_request_for_alert(alerts.camera())
            desk.resolve_request(request.request_id)
            time.sleep(0.002)
        done.set()

    writer = threading.Thread(target=dashboard)
    writer.start()
    totals = []
    while not done.is_set():
        viewer.root.run_next()
        totals.append(int(viewer.displayed()["summary"].split()[0]) if viewer.displayed()["summary"][0].isdigit() else 0)
    writer.join()
    viewer.root.run_next()

    assert totals == sorted(totals)  # the count only ever goes up: no call is lost or double counted mid-write
    assert viewer.displayed()["summary"] == "30 calls: 0 open, 30 resolved"


def test_refreshing_while_lines_arrive_in_pieces(open_viewer, log, make_call):
    # each line reaches the disk in two pieces, so refreshes keep landing mid-line
    viewer = open_viewer()
    done = threading.Event()

    def slow_writer():
        for n in range(8, 28):
            line = json.dumps(make_call(n, "CAMERA_FAILURE", "T1", "IN_PROGRESS", "10:10")) + "\n"
            with open(log.path, "a", encoding="utf-8") as f:
                f.write(line[:20])
                f.flush()
                time.sleep(0.003)
                f.write(line[20:])
        done.set()

    writer = threading.Thread(target=slow_writer)
    writer.start()
    totals = []
    while not done.is_set():
        viewer.root.run_next()
        totals.append(int(viewer.displayed()["summary"].split()[0]))
    writer.join()
    viewer.root.run_next()

    assert totals == sorted(totals) and min(totals) >= 7
    assert viewer.displayed()["summary"] == "27 calls: 23 open, 4 resolved"


def test_a_broken_line_is_reported_once_not_on_every_refresh(open_viewer, log, make_call, logged):
    # a run killed mid-write left a fragment; later runs appended after it
    with open(log.path, "a", encoding="utf-8") as f:
        f.write('{"request_id": "MR-0050", "sta\n')
    log.append(make_call(8, "CAMERA_FAILURE", "T3", "IN_PROGRESS", "10:10"))

    viewer = open_viewer()
    for _ in range(5):
        viewer.root.run_next()

    output = logged()
    assert output.count("Skipping corrupt line 8") == 1
    assert viewer.displayed()["summary"] == "8 calls: 4 open, 4 resolved"  # the calls around it still count


def test_a_new_broken_line_is_still_reported(open_viewer, log, logged):
    viewer = open_viewer()
    viewer.root.run_next()
    with open(log.path, "a", encoding="utf-8") as f:
        f.write('{"request_id": "MR-0051", "sta\n')  # broken while the viewer is running
    for _ in range(3):
        viewer.root.run_next()

    assert logged().count("Skipping corrupt line 8") == 1


@pytest.mark.parametrize("bad_field", BAD_FIELDS)
def test_a_wrongly_typed_call_doesnt_stop_the_refreshes(bad_field, open_viewer, log, make_call, make_bad_call,
                                                        logged):
    viewer = open_viewer()
    before = viewer.displayed()
    log.append(make_bad_call(8, "T3", bad_field))  # line 8

    for _ in range(3):
        viewer.root.run_next()  # must not crash the window

    assert len(viewer.root.scheduled) == 1  # it keeps refreshing
    assert viewer.displayed() == before     # the unreadable call is skipped
    log.append(make_call(9, "CAMERA_FAILURE", "T3", "IN_PROGRESS", "10:20"))
    viewer.root.run_next()
    assert viewer.displayed()["summary"] == "8 calls: 4 open, 4 resolved"  # later calls still appear
    assert logged().count("line 8") == 1  # reported once, not on every refresh


def test_the_viewer_opens_on_a_log_that_already_has_a_wrongly_typed_call(open_viewer, log, make_bad_call, stats):
    log.append(make_bad_call(8, "T3"))
    assert open_viewer().displayed()["summary"] == format_summary(stats)  # the 7 readable calls


def test_the_viewer_never_writes_to_the_log(open_viewer, log):
    before = open(log.path, "rb").read()
    viewer = open_viewer()
    for _ in range(3):
        viewer.root.run_next()
    viewer.close()
    assert open(log.path, "rb").read() == before  # read-only: it can't clash with the dashboard's writes


# --- starting it: `python main.py --stats` ---

def test_run_stats_viewer_opens_the_real_files_by_default(monkeypatch):
    monkeypatch.setattr("hardware_monitor.stats_viewer.tk", FAKE_TK)
    viewer = stats_viewer.run_stats_viewer()  # only reads the real files

    assert (viewer.log_path, viewer.technicians_path) == (MAINTENANCE_LOG_PATH, TECHNICIANS_PATH)
    assert viewer.root.loop_started


def test_main_stats_flag_starts_only_the_viewer(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main.py", "--stats"])
    monkeypatch.setattr("hardware_monitor.stats_viewer.tk", FAKE_TK)  # never a real window, even if the wiring is wrong
    with mock.patch("hardware_monitor.stats_viewer.run_stats_viewer") as viewer, \
            mock.patch("main.run_demo") as demo, \
            mock.patch("main.publish_maintenance_reports") as reports:
        main.main()

    viewer.assert_called_once_with()
    demo.assert_not_called()
    reports.assert_not_called()


# --- opening with the dashboard: `python main.py` ---

@pytest.fixture
def popen():
    """Mocks subprocess.Popen, the external dependency that starts processes."""
    with mock.patch("hardware_monitor.stats_viewer.subprocess.Popen", autospec=True) as started:
        started.return_value.poll.return_value = None  # still running
        yield started


def test_the_viewer_is_launched_as_its_own_process(popen):
    process = stats_viewer.launch_in_background()

    popen.assert_called_once_with([sys.executable, stats_viewer.MAIN_SCRIPT, "--stats"])
    assert process is popen.return_value


def test_the_launched_script_is_this_projects_main_py():
    assert os.path.samefile(stats_viewer.MAIN_SCRIPT, main.__file__)


def test_a_viewer_that_cannot_start_is_reported_not_fatal(popen, logged):
    popen.side_effect = OSError("no such file")
    assert stats_viewer.launch_in_background() is None
    assert "Could not open the statistics window" in logged()


def test_stopping_closes_a_running_viewer(popen):
    process = stats_viewer.launch_in_background()
    stats_viewer.stop_background_viewer(process)

    process.terminate.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=stats_viewer.STOP_TIMEOUT_SECONDS)
    process.kill.assert_not_called()


def test_stopping_a_viewer_that_wont_close_kills_it(popen):
    process = stats_viewer.launch_in_background()
    process.wait.side_effect = [subprocess.TimeoutExpired("main.py", stats_viewer.STOP_TIMEOUT_SECONDS), 0]
    stats_viewer.stop_background_viewer(process)
    process.kill.assert_called_once_with()


def test_a_viewer_the_user_already_closed_is_left_alone(popen):
    process = stats_viewer.launch_in_background()
    process.poll.return_value = 0  # the window was closed by hand
    stats_viewer.stop_background_viewer(process)
    process.terminate.assert_not_called()


def test_stopping_nothing_does_nothing():
    stats_viewer.stop_background_viewer(None)  # the viewer never started: no error


@pytest.fixture
def program(monkeypatch, popen):
    """Runs main.main() with the dashboard window and the demo mocked out."""
    monkeypatch.setattr("hardware_monitor.stats_viewer.tk", FAKE_TK)
    calls = SimpleNamespace(popen=popen)

    def run(*flags, dashboard_error=None):
        monkeypatch.setattr(sys, "argv", ["main.py", *flags])
        with mock.patch("hardware_monitor.dashboard.HardwareMonitorApp") as app_class, \
                mock.patch("main.run_demo") as demo, \
                mock.patch("main.publish_maintenance_reports") as reports:
            app = app_class.return_value
            calls.viewer_open_when_dashboard_started = None

            def start():
                calls.viewer_open_when_dashboard_started = popen.called
                if dashboard_error:
                    raise dashboard_error

            app.start.side_effect = start
            calls.app, calls.demo, calls.reports = app, demo, reports
            main.main()
        return calls

    return run


def test_the_program_opens_the_viewer_next_to_the_dashboard(program):
    calls = program()

    calls.popen.assert_called_once_with([sys.executable, stats_viewer.MAIN_SCRIPT, "--stats"])
    assert calls.viewer_open_when_dashboard_started is True  # opened first, so it's there while monitoring
    calls.app.start.assert_called_once_with()


def test_closing_the_dashboard_closes_the_viewer(program):
    calls = program()
    viewer = calls.popen.return_value
    viewer.terminate.assert_called_once_with()
    calls.reports.assert_called_once_with()  # the history report is still published


def test_the_viewer_is_closed_even_if_the_dashboard_crashes(program, popen):
    with pytest.raises(RuntimeError, match="dashboard crashed"):
        program(dashboard_error=RuntimeError("dashboard crashed"))
    # program() raised, so look at the mock directly
    popen.return_value.terminate.assert_called_once_with()


def test_the_dashboard_still_runs_if_the_viewer_cannot_start(program, popen, capsys):
    popen.side_effect = OSError("no such file")
    calls = program()
    calls.app.start.assert_called_once_with()
    assert "Could not open the statistics window" in capsys.readouterr().out


def test_no_stats_opens_only_the_dashboard(program):
    calls = program("--no-stats")
    calls.popen.assert_not_called()
    calls.app.start.assert_called_once_with()


@pytest.mark.parametrize("flag", ["--no-dashboard", "--report"])
def test_modes_without_the_dashboard_open_no_viewer(program, flag):
    calls = program(flag)
    calls.popen.assert_not_called()


@pytest.mark.parametrize("flag", ["--stats", "--no-stats"])
def test_the_flag_is_listed_in_the_help(capsys, monkeypatch, flag):
    monkeypatch.setattr(sys, "argv", ["main.py", "--help"])
    with pytest.raises(SystemExit):
        main.main()
    assert flag in capsys.readouterr().out
