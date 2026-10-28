"""Safe logging: the program documents its actions, events and failures with
Python's logging module, and nothing secret reaches a log.

Every module logs through `logging.getLogger(__name__)`. configure_logging()
(called once by main.py) sends those records to the console and to
logs/monitor.log, both through a RedactingFilter: anything shaped like a
token or key, and every secret config registered, becomes [REDACTED], and
the home folder (which holds the user's name) becomes ~."""

import logging
import os
import re
import sys
import threading

from .config import MONITOR_LOG_PATH

# what real secrets look like: GitHub tokens, Google API keys, Fernet tokens and Fernet keys
SECRET_PATTERNS = [
    r"ghp_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"AIza[0-9A-Za-z_-]{30,}",
    r"gAAAAA[A-Za-z0-9_=-]{20,}",
    r"[A-Za-z0-9_-]{43}=",
]
_SECRET_RE = re.compile("|".join(f"(?:{p})" for p in SECRET_PATTERNS))
MIN_SECRET_LENGTH = 8  # masking a shorter value would wreck ordinary messages
REDACTED = "[REDACTED]"
HOME = os.path.expanduser("~")
# how a path under it can appear: as is, with forward slashes, or quoted by repr() (an OSError)
_HOME_FORMS = sorted({HOME, HOME.replace("\\", "/"), HOME.replace("\\", "\\\\")}, key=len, reverse=True)
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
CONSOLE_FORMAT = "%(levelname)s: %(message)s"

_secrets = set()
_secrets_lock = threading.Lock()  # config registers from the dashboard's threads too


def register_secret(value):
    """From now on, redact() masks this exact value too."""
    if value and len(value) >= MIN_SECRET_LENGTH:
        with _secrets_lock:
            _secrets.add(value)


def forget_secrets():
    with _secrets_lock:
        _secrets.clear()


def redact(text):
    with _secrets_lock:
        secrets = sorted(_secrets, key=len, reverse=True)  # longest first: one secret may contain another
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    text = _SECRET_RE.sub(REDACTED, text)
    if len(HOME) > 3:  # not a bare "/" or "C:\\"
        for home in _HOME_FORMS:
            text = text.replace(home, "~")
    return text


class RedactingFilter(logging.Filter):
    """Redacts a record's message (its arguments filled in) and its traceback."""

    def filter(self, record):
        record.msg, record.args = redact(record.getMessage()), None
        if record.exc_info:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def configure_logging(log_path=None, level=logging.INFO):
    """Sends the package's records to the console and to log_path
    (MONITOR_LOG_PATH by default). Calling it again replaces the handlers
    it added, rather than adding more."""
    log_path = log_path or MONITOR_LOG_PATH
    logger = logging.getLogger("hardware_monitor")
    for handler in [h for h in logger.handlers if getattr(h, "_safe_logging", False)]:
        logger.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler(sys.stdout)  # where the program's messages always went
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    log_file = logging.FileHandler(log_path, encoding="utf-8")
    log_file.setFormatter(logging.Formatter(LOG_FORMAT))
    for handler in (console, log_file):
        handler._safe_logging = True
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger
