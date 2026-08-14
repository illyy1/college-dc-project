======================================
Room Hardware Monitor
======================================

This program is designed to monitor your computer's hardware and display on-screen alerts if your microphone or camera is disconnected. It has been upgraded to include silence detection, automatic GitHub alerting, and a user interface.

Features
--------
* Camera Monitoring: Detects if the camera is unplugged or failing to capture an image.
* Microphone Monitoring: Detects if the microphone is disconnected.
* Silence Detection: Alerts you if your microphone registers zero/low audio for over 15 seconds (e.g., if you are accidentally muted).
* GitHub Integration: Automatically posts a hardware alert to your GitHub repository's Issues tab when a failure is detected.
* Pause Functionality: A "Pause for Break" button allows you to temporarily suspend monitoring without triggering alerts.

Prerequisites & Installation
----------------------------
In order for this program to run, you must first install the required dependencies. Copy and paste the following line into your terminal on PyCharm (or command prompt) to get started:

pip install sounddevice numpy opencv-python requests python-dotenv cryptography pyinstaller

Configuration & Setup
---------------------
Because this script securely connects to GitHub, you need to set up your environment variables before running it:

1. Create a file named `.env` in the exact same folder as your Python script.
2. Inside the `.env` file, add your encrypted GitHub token in the following format:
   GITHUB_TOKEN=your_encrypted_token_string_here
3. Save the file.

(Note: The script uses a specific `ENCRYPTION_KEY` to decrypt the token at runtime. Ensure your encrypted token matches the key provided in the code.)

Usage
-----
Run the script as you normally would in PyCharm or via the terminal.

A small control window will appear on your screen. As long as this window says "Monitoring Active", the script is checking your hardware in the background. If a hardware failure or silence is detected, a red popup window will appear and stay on top of your other applications until the issue is resolved.

To stop the program entirely, simply close the main control window.