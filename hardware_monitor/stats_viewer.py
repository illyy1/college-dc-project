"""Live statistics window. `python main.py --stats` opens it alone;
`python main.py` opens it as a separate process next to the dashboard. It
re-reads the maintenance log every few seconds, so it follows the calls the
dashboard opens and resolves. It shows a summary, two pie charts (each
technician's share of the calls and each alert type's share), the
per-technician spread, and the recent calls with who took each one."""

import colorsys
import os
import subprocess
import sys
import tkinter as tk
from dataclasses import dataclass

from .config import MAINTENANCE_LOG_PATH, TECHNICIANS_PATH
from .stats import load_stats

REFRESH_SECONDS = 2
BAR, BAR_WIDTH = "█", 20  # the busiest technician's bar is BAR_WIDTH blocks long
PIE_COLORS = ("#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1", "#9c755f")
PIE_DIAMETER, PIE_MARGIN = 180, 12
LEGEND_ROW_HEIGHT = 22
NO_CALLS = "No calls yet"
TEXT_FONT = "TkFixedFont"  # monospaced, so the columns line up
MAIN_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
STOP_TIMEOUT_SECONDS = 5


# --- the text of each section ---

def _calls(count, column=False):
    """'1 call' / '3 calls'. As a column ('  1 call ' / '  3 calls') the
    numbers and the word line up from row to row."""
    word = "call" if count == 1 else "calls"
    return f"{count:>3} {word:<5}" if column else f"{count} {word}"


def format_summary(stats):
    if not stats.total_calls:
        return "No maintenance calls yet."
    return f"{_calls(stats.total_calls)}: {stats.open_calls} open, {stats.resolved_calls} resolved"


def format_alert_types(stats):
    if not stats.alert_types:
        return "No alerts yet."
    return "\n".join(
        f"{t.alert_type:<12}  {_calls(t.total, column=True)}  {t.open:>3} open  {t.resolved:>3} resolved"
        for t in stats.alert_types
    )


def format_technicians(stats):
    if not stats.technicians:
        return "No technicians."
    width = max(len(t.name) for t in stats.technicians)
    busiest = max(t.total for t in stats.technicians)
    lines = []
    for t in stats.technicians:
        bar = BAR * round(BAR_WIDTH * t.total / busiest) if busiest else ""
        lines.append(f"{t.name:<{width}}  {_calls(t.total, column=True)}  {t.open:>3} open  {t.resolved:>3} resolved  "
                     f"{t.share:5.1f}%  {bar}".rstrip())
    return "\n".join(lines)


def format_recent(stats):
    if not stats.recent_calls:
        return f"{NO_CALLS}."
    return "\n".join(
        f"{c.request_id}  {c.alert_type:<10}  {c.workstation_id or '-':<14}  {c.technician:<16}  {c.status}"
        for c in stats.recent_calls
    )


# --- pie charts ---

@dataclass(frozen=True)
class Slice:
    label: str
    value: int
    percent: float
    start: float   # degrees, counterclockwise from 3 o'clock, as tk.Canvas.create_arc takes it
    extent: float
    color: str


def pie_colors():
    """The palette's colours first, then as many more as needed, each a new
    hue: consecutive golden-ratio steps round the colour wheel stay far
    apart, and the lightness alternates so neighbours differ more."""
    yield from PIE_COLORS
    step = 0
    while True:
        step += 1
        hue = (step * 0.618033988749895) % 1
        lightness = (0.45, 0.62)[step % 2]
        red, green, blue = colorsys.hls_to_rgb(hue, lightness, 0.55)
        yield f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def pie_slices(parts):
    """(label, value) pairs -> slices from 12 o'clock that fill the circle.
    Parts with no value get no slice. Every slice gets its own colour, however
    many there are, so no two technicians ever look alike."""
    parts = [(label, value) for label, value in parts if value > 0]
    total = sum(value for _, value in parts)
    colors, used = pie_colors(), set()
    slices, angle = [], 90.0
    for label, value in parts:
        color = next(colors)
        while color in used:  # a generated colour could round to one already taken
            color = next(colors)
        used.add(color)
        extent = 360 * value / total
        slices.append(Slice(label, value, 100 * value / total, angle, extent, color))
        angle += extent
    return slices


def pie_canvas_height(entries):
    """Tall enough for the pie and for a legend of `entries` lines."""
    pie_bottom = PIE_MARGIN + PIE_DIAMETER
    legend_bottom = PIE_MARGIN + 10 + (entries - 1) * LEGEND_ROW_HEIGHT + 6
    return max(pie_bottom, legend_bottom) + PIE_MARGIN


def draw_pie(canvas, slices):
    """Draws the pie and a color-keyed legend, replacing what was there. The
    canvas grows (or shrinks back) so the whole legend fits."""
    canvas.delete("all")
    canvas.config(height=pie_canvas_height(len(slices)))
    left = top = PIE_MARGIN
    right = bottom = PIE_MARGIN + PIE_DIAMETER
    if not slices:
        canvas.create_text((left + right) / 2, (top + bottom) / 2, text=NO_CALLS, fill="gray")
        return
    if len(slices) == 1:
        # tk.Canvas draws nothing for a 360 degree arc, so a whole pie is a circle
        canvas.create_oval(left, top, right, bottom, fill=slices[0].color, outline="white")
    else:
        for piece in slices:
            canvas.create_arc(left, top, right, bottom, start=piece.start, extent=piece.extent,
                              fill=piece.color, outline="white")
    legend_x = right + 2 * PIE_MARGIN
    for row, piece in enumerate(slices):
        y = top + 10 + row * LEGEND_ROW_HEIGHT
        canvas.create_rectangle(legend_x, y - 6, legend_x + 12, y + 6, fill=piece.color, outline="")
        canvas.create_text(legend_x + 20, y, text=f"{piece.label}  {piece.percent:.1f}%", anchor="w")


# --- the window ---

class StatsViewer:
    TEXT_SECTIONS = (("summary", "Summary"), ("alert_types", "Alerts by type"),
                     ("technicians", "Calls per technician"), ("recent", "Recent calls and who took them"))

    def __init__(self, log_path, technicians_path, refresh_seconds=REFRESH_SECONDS):
        self.log_path = log_path
        self.technicians_path = technicians_path
        self.refresh_ms = int(refresh_seconds * 1000)
        self.running = True
        self._pending = None
        self._summary = ""                     # the summary without any read warning
        self._text = {key: "" for key, _ in self.TEXT_SECTIONS}
        self._pies = {"technicians": [], "alert_types": []}
        self._reported_lines = set()           # broken log lines already reported, so each is reported once

        self.root = tk.Tk()
        self.root.title("Live Alert Statistics")
        self.root.geometry("900x820")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._labels = {}
        self._add_text_section("summary", "Summary")
        pies = tk.Frame(self.root)
        pies.pack(fill="x", padx=8)
        self.technician_pie_canvas = self._add_pie(pies, "Share of calls per technician")
        self.alert_type_pie_canvas = self._add_pie(pies, "Share of calls per alert type")
        for key, title in self.TEXT_SECTIONS[1:]:
            self._add_text_section(key, title)

        self.refresh()

    def _add_text_section(self, key, title):
        frame = tk.LabelFrame(self.root, text=title, padx=8, pady=4)
        frame.pack(fill="x", padx=8, pady=4)
        label = tk.Label(frame, text="", font=TEXT_FONT, justify="left", anchor="w")
        label.pack(fill="x")
        self._labels[key] = label

    def _add_pie(self, parent, title):
        frame = tk.LabelFrame(parent, text=title, padx=4, pady=4)
        frame.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        canvas = tk.Canvas(frame, width=PIE_DIAMETER + 250, height=pie_canvas_height(0),
                           bg="white", highlightthickness=0)
        canvas.pack()
        return canvas

    def displayed(self):
        """The text currently on screen, per section."""
        return dict(self._text)

    def pies(self):
        """The slices of the two pie charts currently drawn."""
        return {name: list(slices) for name, slices in self._pies.items()}

    def refresh(self):
        """Re-reads the log, redraws everything, and schedules the next refresh."""
        if not self.running:
            return  # a refresh queued just before the window closed
        try:
            stats = load_stats(self.log_path, self.technicians_path, reported=self._reported_lines)
        except (OSError, ValueError) as error:
            # e.g. the log is briefly locked: keep the last numbers and say so
            self._set_text("summary", f"{self._summary}\nCould not read the maintenance log ({error}); "
                                      f"showing the last numbers.".lstrip("\n"))
        else:
            self._show(stats)
        self._pending = self.root.after(self.refresh_ms, self.refresh)

    def _show(self, stats):
        self._summary = format_summary(stats)
        self._set_text("summary", self._summary)
        self._set_text("alert_types", format_alert_types(stats))
        self._set_text("technicians", format_technicians(stats))
        self._set_text("recent", format_recent(stats))
        self._pies = {
            "technicians": pie_slices([(t.name, t.total) for t in stats.technicians]),
            "alert_types": pie_slices([(t.alert_type, t.total) for t in stats.alert_types]),
        }
        draw_pie(self.technician_pie_canvas, self._pies["technicians"])
        draw_pie(self.alert_type_pie_canvas, self._pies["alert_types"])

    def _set_text(self, key, text):
        self._text[key] = text
        self._labels[key].config(text=text)

    def run(self):
        self.root.mainloop()

    def close(self):
        self.running = False
        if self._pending is not None:
            try:
                self.root.after_cancel(self._pending)
            except tk.TclError:
                pass
        self.root.destroy()


def run_stats_viewer(log_path=MAINTENANCE_LOG_PATH, technicians_path=TECHNICIANS_PATH):
    """Opens the viewer on the real log and technician file; returns it once closed."""
    viewer = StatsViewer(log_path, technicians_path)
    viewer.run()
    return viewer


# --- running next to the dashboard ---
# The viewer has its own Tk window and event loop, so it runs as a separate
# process rather than a second window inside the dashboard's process.

def launch_in_background():
    """Starts the viewer (`main.py --stats`) with this same Python. Returns
    the process, or None if it could not start: the dashboard runs anyway."""
    try:
        return subprocess.Popen([sys.executable, MAIN_SCRIPT, "--stats"])
    except OSError as error:
        print(f"Could not open the statistics window ({error}); the dashboard runs without it.")
        return None


def stop_background_viewer(process):
    """Closes the viewer if it is still open; kills it if it won't close."""
    if process is None or process.poll() is not None:
        return  # never started, or the window was already closed by hand
    process.terminate()
    try:
        process.wait(timeout=STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
