import os
import socket

# --- Live monitoring tuning ---
SILENCE_THRESHOLD = 0.01
SILENCE_DURATION = 8

# --- Alert popups: frequent enough to feel busy, never flooding the screen ---
MAX_VISIBLE_ALERTS = 2          # further faults wait until a popup closes
ALERT_MIN_DISPLAY_SECONDS = 5   # a popup stays at least this long, so it never just flashes
ALERT_COOLDOWN_SECONDS = 10     # the same fault can't pop up again this soon after closing
GITHUB_ISSUE_COOLDOWN_SECONDS = 600  # at most one GitHub issue per fault type in this window
MAX_QUIET_SECONDS = 45         # no popup for this long -> a fault is forced, so there is at least one alert a minute
GITHUB_REPO = "illyy1/college-dc-project"
COMPUTER_NAME = socket.gethostname()

# --- Paths ---
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_DATA_PATH = os.path.join(_PROJECT_ROOT, "data", "sample_data.jsonl")
LOG_PATH = os.path.join(_PROJECT_ROOT, "logs", "hardware_history.jsonl")
TECHNICIANS_PATH = os.path.join(_PROJECT_ROOT, "data", "technicians.jsonl")
MAINTENANCE_LOG_PATH = os.path.join(_PROJECT_ROOT, "logs", "maintenance_requests.jsonl")
MAINTENANCE_REPORT_CSV_PATH = os.path.join(_PROJECT_ROOT, "logs", "maintenance_report.csv")
# the main.py demo writes its alerts and calls here so they never mix with the real history
DEMO_LOG_PATH = os.path.join(_PROJECT_ROOT, "logs", "demo_hardware_history.jsonl")
DEMO_MAINTENANCE_LOG_PATH = os.path.join(_PROJECT_ROOT, "logs", "demo_maintenance_requests.jsonl")


def get_github_token():
    """Decrypts the GitHub token from .env. Returns None if unavailable.

    python-dotenv and cryptography are imported here rather than at the top,
    because only the live dashboard needs them: the demo runs without them."""
    from dotenv import load_dotenv
    from cryptography.fernet import Fernet

    load_dotenv()
    encrypted_token = os.getenv("GITHUB_TOKEN")
    crypt_key = os.getenv("ENCRYPTION_KEY")

    if not encrypted_token or not crypt_key:
        print("Warning: GITHUB_TOKEN or ENCRYPTION_KEY not found in .env file.")
        return None

    try:
        cipher_suite = Fernet(crypt_key.encode("utf-8"))
        # a token saved with a trailing newline would be rejected by requests as a header value
        return cipher_suite.decrypt(encrypted_token.encode("utf-8")).decode("utf-8").strip()
    except Exception as e:
        print(f"Decryption failed. Check your .env file and key. Error: {e}")
        return None
