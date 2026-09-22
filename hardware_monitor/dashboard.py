"""Live monitoring dashboard: background-threaded mic/camera checks, a
Tkinter alert UI, and GitHub Issue alerting. Mic/camera input is simulated
via synthetic_sources.py (rather than real hardware) so the full alert
pipeline can be exercised and tested on demand. See README.md > Future
Expansion for swapping this back to real hardware monitoring."""

import time
import threading
import tkinter as tk
from datetime import datetime

import requests

from .config import (
    SILENCE_THRESHOLD, SILENCE_DURATION,
    GITHUB_REPO, COMPUTER_NAME, get_github_token,
)
from .synthetic_sources import SyntheticMicrophoneSource, SyntheticCameraSource


class HardwareMonitorApp:
    def __init__(self):
        self.github_token = get_github_token()

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
                self.hide_alert(key)
        else:
            self.status_lbl.config(text="Monitoring Active", fg="green")
            self.pause_btn.config(text="Pause for Break")
            self.silence_start = time.time()

    def send_github_alert(self, alert_key, title, message):
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
            payload = {
                "title": f"\U0001f6a8 [{COMPUTER_NAME}] {title}",
                "body": f"### Hardware Alert Triggered\n- **Computer:** `{COMPUTER_NAME}`\n- **Time:** {timestamp}\n- **Component:** `{alert_key}`\n- **Details:** {message}",
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

    def show_alert(self, alert_key, title, message):
        if self.active_alerts.get(alert_key) is not None:
            return

        self.active_alerts[alert_key] = "pending"

        def _create():
            self.send_github_alert(alert_key, title, message)

            alert_win = tk.Toplevel(self.root)
            alert_win.title(title)
            alert_win.geometry("400x150")
            alert_win.attributes("-topmost", True)
            alert_win.configure(bg="#ffcccc")

            tk.Label(alert_win, text=title, font=("Arial", 14, "bold"), bg="#ffcccc", fg="red").pack(pady=10)
            tk.Label(alert_win, text=message, font=("Arial", 12), bg="#ffcccc").pack(pady=10)

            self.active_alerts[alert_key] = alert_win

        self.root.after(0, _create)

    def hide_alert(self, alert_key):
        if self.active_alerts.get(alert_key) is None:
            return

        def _destroy():
            win = self.active_alerts.get(alert_key)
            if isinstance(win, tk.Toplevel) and win.winfo_exists():
                win.destroy()
            self.active_alerts[alert_key] = None

        self.root.after(0, _destroy)

    def monitor_mic(self):
        mic_source = SyntheticMicrophoneSource()

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
                if self.active_alerts.get("mic_silence") is not None:
                    self.hide_alert("mic_silence")

            time.sleep(1)

    def monitor_camera(self):
        camera_source = SyntheticCameraSource()

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

    def start(self):
        mic_thread = threading.Thread(target=self.monitor_mic, daemon=True)
        cam_thread = threading.Thread(target=self.monitor_camera, daemon=True)
        mic_thread.start()
        cam_thread.start()
        self.root.mainloop()

    def stop(self):
        self.running = False
        self.root.destroy()
