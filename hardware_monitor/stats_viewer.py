"""Standalone live statistics window (`python main.py --stats`). It re-reads
the maintenance log every few seconds, so it follows the calls a running
dashboard opens and resolves, even from another process. It shows a
summary, two pie charts (each technician's share of the calls and each
alert type's share), the per-technician spread, and the recent calls with
who took each one."""

import tkinter as tk
from dataclasses import dataclass

from .config import MAINTENANCE_LOG_PATH, TECHNICIANS_PATH
from .stats import load_stats

REFRESH_SECONDS = 2
BAR, BAR_WIDTH = "█", 20  # the busiest technician's bar is BAR_WIDTH blocks long
PIE_COLORS = ("#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1", "#9c755f")
PIE_DIAMETER, PIE_MARGIN = 180, 12
NO_CALLS = "No calls yet"
TEXT_FONT = "TkFixedFont"  # monospaced, so the columns line up


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


def pie_slices(parts):
    """(label, value) pairs -> slices from 12 o'clock that fill the circle.
    Parts with no value get no slice."""
    parts = [(label, value) for label, value in parts if value > 0]
    total = sum(value for _, value in parts)
    slices, angle = [], 90.0
    for index, (label, value) in enumerate(parts):
        extent = 360 * value / total
        slices.append(Slice(label, value, 100 * value / total, angle, extent, PIE_COLORS[index % len(PIE_COLORS)]))
        angle += extent
    return slices


def draw_pie(canvas, slices):
    """Draws the pie and a color-keyed legend, replacing what was there."""
    canvas.delete("all")
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
        y = top + 10 + row * 22
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
        canvas = tk.Canvas(frame, width=PIE_DIAMETER + 250, height=PIE_DIAMETER + 2 * PIE_MARGIN,
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
