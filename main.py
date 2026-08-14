import sounddevice as sd
import numpy as np
import time
import threading
import tkinter as tk
import cv2
import socket
import requests
from datetime import datetime
import os
from dotenv import load_dotenv
from cryptography.fernet import Fernet

# ==========================================
# --- SECURE TOKEN DECRYPTION BLOCK ---
# ==========================================

# 1. Load the encrypted string from the .env file
load_dotenv()
encrypted_token = os.getenv("GITHUB_TOKEN")

# 2. Your specific generated encryption key
ENCRYPTION_KEY = b'ABv3YwffNTB0VLZaGsvZyobktNe6IhOuU4nelR1WIGw='

# 3. Decrypt the token to use with the GitHub API
try:
    if encrypted_token:
        cipher_suite = Fernet(ENCRYPTION_KEY)
        GITHUB_TOKEN = cipher_suite.decrypt(encrypted_token.encode()).decode()
    else:
        GITHUB_TOKEN = None
        print("Warning: GITHUB_TOKEN not found in .env file.")
except Exception as e:
    print(f"Decryption failed. Check your .env file and key. Error: {e}")
    GITHUB_TOKEN = None

# ==========================================

# --- Configuration ---
# Note: Since the math is now properly using RMS, 0.01 is usually a good starting point for silence.
SILENCE_THRESHOLD = 0.01
SILENCE_DURATION = 15
CAMERA_INDEX = 0
GITHUB_REPO = "illyy1/college-dc-project"
COMPUTER_NAME = socket.gethostname()


class HardwareMonitor:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Room Hardware Monitor")
        self.root.geometry("300x150")
        self.root.protocol("WM_DELETE_WINDOW", self.stop)

        self.running = True
        self.is_paused = False
        self.silence_start = time.time()
        self.is_silent = False

        self.active_alerts = {
            'camera': None,
            'mic_conn': None,
            'mic_silence': None
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
            if not GITHUB_TOKEN:
                print("GitHub Token missing or decryption failed. Alert not sent.")
                return

            url = f"https://api.github.com/repos/{GITHUB_REPO}/issues"
            headers = {
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json"
            }
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            payload = {
                "title": f"🚨 [{COMPUTER_NAME}] {title}",
                "body": f"### Hardware Alert Triggered\n- **Computer:** `{COMPUTER_NAME}`\n- **Time:** {timestamp}\n- **Component:** `{alert_key}`\n- **Details:** {message}",
                "labels": ["hardware-alert", alert_key]
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
        # Prevent threading race conditions from triggering multiple GitHub API calls
        if self.active_alerts.get(alert_key) is not None:
            return

        self.active_alerts[alert_key] = "pending"  # Temporary state block

        def _create():
            # Trigger the GitHub API call
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
        # Only issue the destroy command if an alert is actually registered
        if self.active_alerts.get(alert_key) is None:
            return

        def _destroy():
            win = self.active_alerts.get(alert_key)
            if isinstance(win, tk.Toplevel) and win.winfo_exists():
                win.destroy()
            self.active_alerts[alert_key] = None

        self.root.after(0, _destroy)

    def monitor_mic(self):
        def audio_callback(indata, frames, time_info, status):
            if self.is_paused: return

            # FIX: Use RMS for accurate volume mapping regardless of frame size
            volume = np.sqrt(np.mean(indata ** 2))

            if volume < SILENCE_THRESHOLD:
                if not self.is_silent:
                    self.silence_start = time.time()
                    self.is_silent = True
                elif time.time() - self.silence_start > SILENCE_DURATION:
                    # FIX: Only trigger show_alert if the alert isn't already active
                    if self.active_alerts.get('mic_silence') is None:
                        self.show_alert('mic_silence', "Microphone Silence Detected",
                                        "No input registered. Is the mic muted?")
            else:
                self.is_silent = False
                self.silence_start = time.time()
                # FIX: Only call hide_alert if the alert is currently active to prevent UI freezing
                if self.active_alerts.get('mic_silence') is not None:
                    self.hide_alert('mic_silence')

        while self.running:
            if self.is_paused:
                time.sleep(1)
                continue
            try:
                with sd.InputStream(callback=audio_callback):
                    self.hide_alert('mic_conn')
                    while self.running and not self.is_paused:
                        time.sleep(1)
            except Exception:
                self.show_alert('mic_conn', "Microphone Disconnected",
                                "The microphone was unplugged or lost connection.")
                self.hide_alert('mic_silence')
                time.sleep(3)

    def monitor_camera(self):
        while self.running:
            if self.is_paused:
                time.sleep(1)
                continue
            try:
                cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
                if not cap.isOpened():
                    self.show_alert('camera', "Camera Disconnected", "The camera is unplugged or unavailable.")
                else:
                    ret, frame = cap.read()
                    if ret:
                        self.hide_alert('camera')
                    else:
                        self.show_alert('camera', "Camera Error", "Camera connected but providing no image.")
                cap.release()
            except Exception:
                self.show_alert('camera', "Camera Disconnected", "Error accessing the camera hardware.")
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


if __name__ == "__main__":
    monitor = HardwareMonitor()
    monitor.start()