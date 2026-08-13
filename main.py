import sounddevice as sd
import numpy as np
import time
import threading
import tkinter as tk
import cv2

# --- Configuration ---
SILENCE_THRESHOLD = 0.005  # Adjust based on room's ambient noise
SILENCE_DURATION = 15  # Seconds of silence before warning
CAMERA_INDEX = 0  # Default camera index


class HardwareMonitor:
    def __init__(self):
        # Main Control Panel Window
        self.root = tk.Tk()
        self.root.title("Room Hardware Monitor")
        self.root.geometry("300x150")
        self.root.protocol("WM_DELETE_WINDOW", self.stop)

        # State variables
        self.running = True
        self.is_paused = False
        self.silence_start = time.time()
        self.is_silent = False

        # Dictionary to keep track of our active custom alert windows
        self.active_alerts = {
            'camera': None,
            'mic_conn': None,
            'mic_silence': None
        }

        # UI Elements
        self.status_lbl = tk.Label(self.root, text="Monitoring Active", fg="green", font=("Arial", 14, "bold"))
        self.status_lbl.pack(pady=20)

        self.pause_btn = tk.Button(self.root, text="Pause for Break", command=self.toggle_pause, font=("Arial", 12))
        self.pause_btn.pack(pady=10)

    def toggle_pause(self):
        """Toggles the monitoring state for academic breaks."""
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.status_lbl.config(text="Monitoring Paused", fg="orange")
            self.pause_btn.config(text="Resume Monitoring")
            # Clear all active alerts while paused
            for key in self.active_alerts:
                self.hide_alert(key)
        else:
            self.status_lbl.config(text="Monitoring Active", fg="green")
            self.pause_btn.config(text="Pause for Break")
            self.silence_start = time.time()  # Reset silence timer

    def show_alert(self, alert_key, title, message):
        """Creates an alert window if one doesn't already exist for this issue."""

        def _create():
            if self.active_alerts[alert_key] is None or not self.active_alerts[alert_key].winfo_exists():
                alert_win = tk.Toplevel(self.root)
                alert_win.title(title)
                alert_win.geometry("400x150")
                alert_win.attributes("-topmost", True)  # Keep on top of other windows
                alert_win.configure(bg="#ffcccc")  # Light red background

                tk.Label(alert_win, text=title, font=("Arial", 14, "bold"), bg="#ffcccc", fg="red").pack(pady=10)
                tk.Label(alert_win, text=message, font=("Arial", 12), bg="#ffcccc").pack(pady=10)

                self.active_alerts[alert_key] = alert_win

        # Ensure Tkinter UI creation happens on the main thread
        self.root.after(0, _create)

    def hide_alert(self, alert_key):
        """Destroys the alert window if the issue is resolved."""

        def _destroy():
            win = self.active_alerts.get(alert_key)
            if win is not None and win.winfo_exists():
                win.destroy()
            self.active_alerts[alert_key] = None

        self.root.after(0, _destroy)

    def monitor_mic(self):
        """Continuously listens to the microphone."""

        def audio_callback(indata, frames, time_info, status):
            if self.is_paused:
                return

            volume = np.linalg.norm(indata) / frames

            if volume < SILENCE_THRESHOLD:
                if not self.is_silent:
                    self.silence_start = time.time()
                    self.is_silent = True
                elif time.time() - self.silence_start > SILENCE_DURATION:
                    self.show_alert('mic_silence', "Microphone Silence Detected",
                                    "No input registered. Is the mic muted?")
            else:
                self.is_silent = False
                self.silence_start = time.time()
                self.hide_alert('mic_silence')  # Hide alert immediately when noise is detected

        while self.running:
            if self.is_paused:
                time.sleep(1)
                continue

            try:
                # Attempt to open the mic stream
                with sd.InputStream(callback=audio_callback):
                    self.hide_alert('mic_conn')  # Connected! Hide disconnect warning

                    # Keep stream alive as long as we aren't paused
                    while self.running and not self.is_paused:
                        time.sleep(1)

            except Exception as e:
                # Exception triggers if mic is unplugged or missing
                self.show_alert('mic_conn', "Microphone Disconnected",
                                "The microphone was unplugged or lost connection.")
                self.hide_alert('mic_silence')  # Don't show silence warning if physically disconnected
                time.sleep(3)  # Wait before trying to reconnect

    def monitor_camera(self):
        """Periodically checks if the camera is connected."""
        while self.running:
            if self.is_paused:
                time.sleep(1)
                continue

            try:
                cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)  # CAP_DSHOW is faster on Windows
                if not cap.isOpened():
                    self.show_alert('camera', "Camera Disconnected", "The camera is unplugged or unavailable.")
                else:
                    ret, frame = cap.read()
                    if ret:
                        self.hide_alert('camera')  # Connection restored, hide warning
                    else:
                        self.show_alert('camera', "Camera Error", "Camera connected but providing no image.")
                cap.release()
            except Exception:
                self.show_alert('camera', "Camera Disconnected", "Error accessing the camera hardware.")

            time.sleep(3)  # Check camera every 3 seconds

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