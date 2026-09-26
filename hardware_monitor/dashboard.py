"""Live monitoring dashboard: background-threaded mic/camera checks, a
Tkinter alert UI, and GitHub Issue alerting. Mic/camera input is simulated
via synthetic_sources.py (rather than real hardware) so the full alert
pipeline can be exercised and tested on demand. See README.md > Future
Expansion for swapping this back to real hardware monitoring."""

import random
import time
import threading
import tkinter as tk
from datetime import datetime

import requests

from .config import (
    SILENCE_THRESHOLD, SILENCE_DURATION,
    MAX_VISIBLE_ALERTS, ALERT_MIN_DISPLAY_SECONDS, ALERT_COOLDOWN_SECONDS, GITHUB_ISSUE_COOLDOWN_SECONDS,
    MAX_QUIET_SECONDS,
    GITHUB_REPO, COMPUTER_NAME, TECHNICIANS_PATH, get_github_token,
)
from .maintenance import MaintenanceDesk
from .processing import Alert, AlertManager
from .repository import load_technicians_from_jsonl
from .synthetic_sources import SyntheticMicrophoneSource, SyntheticCameraSource

# alert_key -> (component_id, error_type, priority) used when opening a maintenance call
ALERT_DETAILS = {
    "camera": ("CAM-LOCAL", "CAMERA_FAILURE", 1),
    "mic_conn": ("MIC-LOCAL", "MICROPHONE_DISCONNECTED", 2),
    "mic_silence": ("MIC-LOCAL", "MICROPHONE_SILENCE", 4),
}


class HardwareMonitorApp:
    def __init__(self, maintenance_desk=None):
        self.github_token = get_github_token()
        self.alert_manager = AlertManager()
        self.maintenance_desk = maintenance_desk or MaintenanceDesk(
            load_technicians_from_jsonl(TECHNICIANS_PATH)
        )
        self.open_requests = {}  # alert_key -> MaintenanceRequest still being handled
        self._adopt_leftover_calls()

        self.root = tk.Tk()
        self.root.title("Room Hardware Monitor")
        self.root.geometry("300x150")
        self.root.protocol("WM_DELETE_WINDOW", self.stop)

        self.running = True
        self.is_paused = False
        self.silence_start = time.time()
        self.is_silent = False

        self.active_alerts = {
            "camera": None,
            "mic_conn": None,
            "mic_silence": None,
        }
        self._popup_lock = threading.Lock()  # mic and camera threads can both try to show a popup
        self._shown_at = {}                  # alert_key -> when its popup appeared
        self._closed_at = {}                 # alert_key -> when its popup was last closed
        self._popup_slots = {}               # alert_key -> screen slot (0 = top), so popups don't overlap
        self._last_issue_at = {}             # alert_key -> when a GitHub issue was last posted for it
        self._last_popup_at = time.time()    # for the quiet-period watchdog

        self.mic_source = SyntheticMicrophoneSource()
        self.camera_source = SyntheticCameraSource()

        self.status_lbl = tk.Label(self.root, text="Monitoring Active", fg="green", font=("Arial", 14, "bold"))
        self.status_lbl.pack(pady=20)

        self.pause_btn = tk.Button(self.root, text="Pause for Break", command=self.toggle_pause, font=("Arial", 12))
        self.pause_btn.pack(pady=10)

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.status_lbl.config(text="Monitoring Paused", fg="orange")
            self.pause_btn.config(text="Resume Monitoring")
            for key in self.active_alerts:
                # a break only hides the popups; the fault (and its call) is still open
                self.hide_alert(key, resolve_call=False)
        else:
            self.status_lbl.config(text="Monitoring Active", fg="green")
            self.pause_btn.config(text="Pause for Break")
            self.silence_start = time.time()

    def _adopt_leftover_calls(self):
        """Takes over calls this machine left open when it last shut down, so
        they are resolved as soon as the fault is seen to be gone. If older
        versions left several calls open for the same fault, the oldest is
        kept and the rest are closed as duplicates."""
        for alert_key, (component_id, error_type, _) in ALERT_DETAILS.items():
            leftovers = self.maintenance_desk.find_open_requests(COMPUTER_NAME, component_id, error_type)
            if not leftovers:
                continue
            self.open_requests[alert_key] = leftovers[0]
            for duplicate in leftovers[1:]:
                self.maintenance_desk.resolve_request(duplicate.request_id)
                print(f"Closed duplicate maintenance call {duplicate.request_id} "
                      f"(same fault as {leftovers[0].request_id}).")

    def open_maintenance_call(self, alert_key, message):
        """Logs an Alert for the fault and opens a maintenance call assigned to
        a technician. Reuses the call already open for this fault, if any.
        Returns (request, is_new)."""
        request = self.open_requests.get(alert_key)
        if request is not None:
            return request, False

        component_id, error_type, priority = ALERT_DETAILS[alert_key]
        alert = self.alert_manager.log_alert(Alert(
            component_id=component_id,
            workstation_id=COMPUTER_NAME,
            error_type=error_type,
            message=message,
            priority=priority,
        ))
        try:
            request = self.maintenance_desk.open_request_for_alert(alert)
        except ValueError as e:
            print(f"Could not open a maintenance call for {alert_key}: {e}")
            return None, True

        self.open_requests[alert_key] = request
        return request, True

    def resolve_maintenance_call(self, alert_key):
        request = self.open_requests.pop(alert_key, None)
        if request is None:
            return
        self.maintenance_desk.resolve_request(request.request_id)
        technician = self.maintenance_desk.technician_for(request)
        print(f"Maintenance call {request.request_id} resolved "
              f"(handled by {technician.name if technician else 'unassigned'}).")

    def send_github_alert(self, alert_key, title, message, request=None):
        """Sends the alert payload to GitHub Issues in a background thread."""

        def _post():
            if not self.github_token:
                print("GitHub Token missing or decryption failed. Alert not sent.")
                return

            url = f"https://api.github.com/repos/{GITHUB_REPO}/issues"
            headers = {
                "Authorization": f"Bearer {self.github_token}",
                "Accept": "application/vnd.github.v3+json",
            }
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            body = f"### Hardware Alert Triggered\n- **Computer:** `{COMPUTER_NAME}`\n- **Time:** {timestamp}\n- **Component:** `{alert_key}`\n- **Details:** {message}"
            if request is not None:
                technician = self.maintenance_desk.technician_for(request)
                body += (f"\n- **Maintenance Call:** `{request.request_id}`"
                         f"\n- **Assigned Technician:** {technician.name if technician else 'unassigned'}")
            payload = {
                "title": f"\U0001f6a8 [{COMPUTER_NAME}] {title}",
                "body": body,
                "labels": ["hardware-alert", alert_key],
            }

            try:
                response = requests.post(url, json=payload, headers=headers, timeout=5)
                if response.status_code == 201:
                    print(f"Alert successfully posted to GitHub for: {alert_key}")
                else:
                    print(f"Failed to post to GitHub. Status code: {response.status_code}")
            except Exception as e:
                print(f"Network error logging to GitHub: {e}")

        threading.Thread(target=_post, daemon=True).start()

    def _reserve_popup_slot(self, alert_key):
        """Claims a screen slot for a new popup. Returns False if the popup
        should not be shown now: already showing, same fault closed too
        recently, or MAX_VISIBLE_ALERTS already on screen (the monitor
        retries on its next check while the fault lasts)."""
        with self._popup_lock:
            if self.active_alerts.get(alert_key) is not None:
                return False
            if time.time() - self._closed_at.get(alert_key, 0) < ALERT_COOLDOWN_SECONDS:
                return False
            used = set(self._popup_slots.values())
            free = [slot for slot in range(MAX_VISIBLE_ALERTS) if slot not in used]
            if not free:
                return False
            self._popup_slots[alert_key] = free[0]
            self._shown_at[alert_key] = time.time()
            self._last_popup_at = time.time()
            self.active_alerts[alert_key] = "pending"
            return True

    def _claim_github_issue(self, alert_key):
        """Rate limit: True (and starts a new window) if no GitHub issue was
        posted for this fault type in the last GITHUB_ISSUE_COOLDOWN_SECONDS.
        The popup and maintenance call still happen either way."""
        with self._popup_lock:
            last = self._last_issue_at.get(alert_key)
            if last is not None and time.time() - last < GITHUB_ISSUE_COOLDOWN_SECONDS:
                minutes_left = (GITHUB_ISSUE_COOLDOWN_SECONDS - (time.time() - last)) / 60
                print(f"GitHub issue for {alert_key} skipped: one was posted recently "
                      f"(next allowed in {minutes_left:.0f} min). The maintenance call was still opened.")
                return False
            self._last_issue_at[alert_key] = time.time()
            return True

    def _run_on_ui_thread(self, callback):
        """Queues callback for the Tk main loop. Once the window is closed the
        monitor threads may still get here; the callback is then dropped."""
        if not self.running:
            return
        try:
            self.root.after(0, callback)
        except (RuntimeError, tk.TclError):
            if self.running:
                raise

    def show_alert(self, alert_key, title, message):
        # a monitor thread may have checked is_paused just before Pause was pressed
        if self.is_paused or not self.running:
            return

        # the call is opened even when the popup has to wait (too many on screen,
        # or the same fault closed moments ago), so no fault is left unrecorded
        request, is_new = self.open_maintenance_call(alert_key, message)
        # a popup re-shown after a break is the same fault — no duplicate issue
        if is_new and self._claim_github_issue(alert_key):
            self.send_github_alert(alert_key, title, message, request)

        if not self._reserve_popup_slot(alert_key):
            return
        slot = self._popup_slots[alert_key]

        def _create():
            with self._popup_lock:
                # Pause (or a hide) released this reservation before it was drawn
                if self.is_paused or self.active_alerts.get(alert_key) != "pending":
                    if self.active_alerts.get(alert_key) == "pending":
                        self.active_alerts[alert_key] = None
                        self._popup_slots.pop(alert_key, None)
                    return

            alert_win = tk.Toplevel(self.root)
            alert_win.title(title)
            # stacked down the right edge of the screen, one slot each
            x = max(0, alert_win.winfo_screenwidth() - 420)
            alert_win.geometry(f"400x180+{x}+{40 + slot * 210}")
            alert_win.attributes("-topmost", True)
            alert_win.configure(bg="#ffcccc")

            tk.Label(alert_win, text=title, font=("Arial", 14, "bold"), bg="#ffcccc", fg="red").pack(pady=10)
            tk.Label(alert_win, text=message, font=("Arial", 12), bg="#ffcccc").pack(pady=10)
            if request is not None:
                technician = self.maintenance_desk.technician_for(request)
                tk.Label(alert_win, font=("Arial", 11), bg="#ffcccc",
                         text=f"Call {request.request_id} assigned to "
                              f"{technician.name if technician else 'unassigned'}").pack()

            self.active_alerts[alert_key] = alert_win

        self._run_on_ui_thread(_create)

    def hide_alert(self, alert_key, resolve_call=True):
        """Closes the popup (and, with resolve_call, its maintenance call)
        once the fault is gone. A popup younger than ALERT_MIN_DISPLAY_SECONDS
        is left up; the monitor calls this again on its next check. Pausing
        passes resolve_call=False and hides immediately."""
        if not self.running:
            return
        popup_open = self.active_alerts.get(alert_key) is not None
        if resolve_call and popup_open:
            if time.time() - self._shown_at.get(alert_key, 0) < ALERT_MIN_DISPLAY_SECONDS:
                return
            self._closed_at[alert_key] = time.time()

        if resolve_call:
            self.resolve_maintenance_call(alert_key)

        if not popup_open:
            return

        def _destroy():
            win = self.active_alerts.get(alert_key)
            if isinstance(win, tk.Toplevel) and win.winfo_exists():
                win.destroy()
            with self._popup_lock:
                self.active_alerts[alert_key] = None
                self._popup_slots.pop(alert_key, None)

        self._run_on_ui_thread(_destroy)

    def monitor_mic(self):
        mic_source = self.mic_source

        while self.running:
            if self.is_paused:
                time.sleep(1)
                continue

            if not mic_source.is_connected():
                self.show_alert("mic_conn", "Microphone Disconnected",
                                 "Synthetic microphone reports disconnected (simulated).")
                self.hide_alert("mic_silence")
                self.is_silent = False
                time.sleep(1)
                continue

            self.hide_alert("mic_conn")
            volume = mic_source.read_volume()

            if volume < SILENCE_THRESHOLD:
                if not self.is_silent:
                    self.silence_start = time.time()
                    self.is_silent = True
                elif time.time() - self.silence_start > SILENCE_DURATION:
                    if self.active_alerts.get("mic_silence") is None:
                        self.show_alert("mic_silence", "Microphone Silence Detected",
                                         "Synthetic input reports no signal (simulated). Is the mic muted?")
            else:
                self.is_silent = False
                self.silence_start = time.time()
                # unconditional: also resolves a silence call adopted from a previous run
                self.hide_alert("mic_silence")

            time.sleep(1)

    def monitor_camera(self):
        camera_source = self.camera_source

        while self.running:
            if self.is_paused:
                time.sleep(1)
                continue

            if not camera_source.is_connected():
                self.show_alert("camera", "Camera Disconnected",
                                 "Synthetic camera reports disconnected (simulated).")
            elif not camera_source.read_frame_ok():
                self.show_alert("camera", "Camera Error",
                                 "Synthetic camera connected but returned no frame (simulated).")
            else:
                self.hide_alert("camera")

            time.sleep(3)

    def keep_room_busy(self):
        """Quiet-period watchdog: if no popup has appeared for
        MAX_QUIET_SECONDS, forces a fault on a device that is free to show one
        (no popup open, not cooling down, a screen slot free), so there is at
        least one alert a minute. A break doesn't count as quiet time."""
        while self.running:
            time.sleep(1)
            if self.is_paused:
                self._last_popup_at = time.time()
                continue
            if time.time() - self._last_popup_at < MAX_QUIET_SECONDS:
                continue
            with self._popup_lock:
                slot_free = len(self._popup_slots) < MAX_VISIBLE_ALERTS
                eligible = [
                    source for key, source in (("mic_conn", self.mic_source), ("camera", self.camera_source))
                    if self.active_alerts.get(key) is None
                    and time.time() - self._closed_at.get(key, 0) >= ALERT_COOLDOWN_SECONDS
                ]
            if slot_free and eligible:
                random.choice(eligible).force_fault()
                self._last_popup_at = time.time()  # give the monitor a moment to notice before forcing again

    def start(self):
        mic_thread = threading.Thread(target=self.monitor_mic, daemon=True)
        cam_thread = threading.Thread(target=self.monitor_camera, daemon=True)
        busy_thread = threading.Thread(target=self.keep_room_busy, daemon=True)
        mic_thread.start()
        cam_thread.start()
        busy_thread.start()
        self.root.mainloop()

    def stop(self):
        self.running = False
        self.root.destroy()
