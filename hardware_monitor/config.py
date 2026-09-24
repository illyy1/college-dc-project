import os
import socket

from dotenv import load_dotenv
from cryptography.fernet import Fernet

load_dotenv()

# --- Live monitoring tuning ---
SILENCE_THRESHOLD = 0.01
SILENCE_DURATION = 15
GITHUB_REPO = "illyy1/college-dc-project"
COMPUTER_NAME = socket.gethostname()

# --- Paths ---
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_DATA_PATH = os.path.join(_PROJECT_ROOT, "data", "sample_data.jsonl")
LOG_PATH = os.path.join(_PROJECT_ROOT, "logs", "hardware_history.jsonl")
TECHNICIANS_PATH = os.path.join(_PROJECT_ROOT, "data", "technicians.jsonl")
MAINTENANCE_LOG_PATH = os.path.join(_PROJECT_ROOT, "logs", "maintenance_requests.jsonl")
MAINTENANCE_REPORT_CSV_PATH = os.path.join(_PROJECT_ROOT, "logs", "maintenance_report.csv")


def get_github_token():
    """Decrypts the GitHub token from .env. Returns None if unavailable."""
    encrypted_token = os.getenv("GITHUB_TOKEN")
    crypt_key = os.getenv("ENCRYPTION_KEY")

    if not encrypted_token or not crypt_key:
        print("Warning: GITHUB_TOKEN or ENCRYPTION_KEY not found in .env file.")
        return None

    try:
        cipher_suite = Fernet(crypt_key.encode("utf-8"))
        return cipher_suite.decrypt(encrypted_token.encode("utf-8")).decode("utf-8")
    except Exception as e:
        print(f"Decryption failed. Check your .env file and key. Error: {e}")
        return None
