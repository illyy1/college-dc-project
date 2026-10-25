"""Entry point only — all logic lives in the hardware_monitor package.

Runs the linear demonstration of the information system (hardware_monitor/
demo.py), then launches the live hardware-monitoring dashboard for this
machine. When the dashboard is closed, the maintenance history report is
printed and exported to CSV.

    python main.py                  demo + live dashboard + history report
    python main.py --no-dashboard   demo + history report, no window (runs start to finish)
    python main.py --report         only print/export the maintenance history report
    python main.py --stats          only open the live alert statistics window
"""

import argparse
import sys

from hardware_monitor.demo import run_demo
from hardware_monitor.reports import publish_maintenance_reports


def main():
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
    args = parser.parse_args()

    if args.report:
        publish_maintenance_reports()
        return

    if args.stats:
        # imported here, like the dashboard, so the other modes work without tkinter
        from hardware_monitor.stats_viewer import run_stats_viewer
        run_stats_viewer()
        return

    run_demo()

    if args.no_dashboard:
        publish_maintenance_reports()
        return

    # imported here so --report and --no-dashboard work without tkinter installed
    from hardware_monitor.dashboard import HardwareMonitorApp

    print("\nLaunching live hardware monitoring dashboard for this workstation...")
    app = HardwareMonitorApp()  # uses the real maintenance log, not the demo one
    try:
        app.start()
    finally:
        # also runs on Ctrl+C; the CSV itself is already refreshed on every call change
        publish_maintenance_reports()


if __name__ == "__main__":
    main()
