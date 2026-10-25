"""Entry point only — all logic lives in the hardware_monitor package.

Runs the linear demonstration of the information system (hardware_monitor/
demo.py), then launches the live hardware-monitoring dashboard for this
machine, with the live alert statistics window next to it. When the
dashboard is closed, the statistics window closes too and the maintenance
history report is printed and exported to CSV.

    python main.py                  demo + live dashboard and statistics window + history report
    python main.py --no-stats       the same, without the statistics window
    python main.py --no-dashboard   demo + history report, no window (runs start to finish)
    python main.py --report         only print/export the maintenance history report
    python main.py --stats          only open the live alert statistics window
"""

import argparse
import sys

from hardware_monitor.demo import run_demo
from hardware_monitor.reports import publish_maintenance_reports
from hardware_monitor.repository import DataFileError


def main():
    """Runs the mode chosen on the command line. Returns the exit code: 0, or
    1 after explaining a file problem: a report that couldn't be written, a data
    file that is missing, broken or unreadable, or a log still locked after the
    retries. Any other error is a bug and is raised as usual."""
    # the Hebrew messages crash on Windows when output is redirected to a file
    # (cp1252 cannot encode them), so always print UTF-8. IDLE and Jupyter
    # replace stdout with their own stream, which has no reconfigure() but
    # already handles Unicode, so those are left alone.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Room hardware monitor")
    parser.add_argument("--report", action="store_true",
                        help="only print and export the maintenance history report")
    parser.add_argument("--no-dashboard", action="store_true",
                        help="run the demo and reports without opening the live dashboard window")
    parser.add_argument("--stats", action="store_true",
                        help="only open the live alert statistics window (follows the maintenance log)")
    parser.add_argument("--no-stats", action="store_true",
                        help="open the dashboard without the statistics window next to it")
    args = parser.parse_args()

    try:
        return _run(args)
    except (OSError, DataFileError) as error:
        print(f"Cannot start: {error}", file=sys.stderr)
        return 1


def _run(args):
    if args.report:
        return 0 if _publish_reports() else 1

    if args.stats:
        # imported here, like the dashboard, so the other modes work without tkinter
        from hardware_monitor.stats_viewer import run_stats_viewer
        run_stats_viewer()
        return 0

    run_demo()

    if args.no_dashboard:
        return 0 if _publish_reports() else 1

    # imported here so --report and --no-dashboard work without tkinter installed
    from hardware_monitor.dashboard import HardwareMonitorApp
    from hardware_monitor.stats_viewer import launch_in_background, stop_background_viewer

    print("\nLaunching live hardware monitoring dashboard for this workstation...")
    app = HardwareMonitorApp()  # uses the real maintenance log, not the demo one
    viewer = None if args.no_stats else launch_in_background()  # follows the same log, in its own window
    try:
        app.start()
    finally:
        # also runs on Ctrl+C; the CSV itself is already refreshed on every call change.
        # _publish_reports never raises, so a crash of the dashboard is not hidden by it
        stop_background_viewer(viewer)
        reports_ok = _publish_reports()
    return 0 if reports_ok else 1


def _publish_reports():
    """publish_maintenance_reports(), explaining a report that can't be written
    instead of ending with a traceback. Returns whether it was written."""
    try:
        publish_maintenance_reports()
        return True
    except OSError as error:
        hint = " Is it open in Excel?" if isinstance(error, PermissionError) else ""
        print(f"Could not write the maintenance report: {error}.{hint} The calls are still in the "
              f"maintenance log; run `python main.py --report` to try again.", file=sys.stderr)
        return False


if __name__ == "__main__":
    sys.exit(main())
