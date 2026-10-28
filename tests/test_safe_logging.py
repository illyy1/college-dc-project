"""TDD specification for safe logging: the program documents its actions,
events and failures with Python's logging module, never exposing a secret
or a person's details.

What hardware_monitor/safe_logging.py must provide:
- SECRET_PATTERNS: regexes for what real secrets look like (GitHub tokens,
  Google API keys, Fernet tokens and keys).
- redact(text) -> text with every match of SECRET_PATTERNS, and every
  registered secret, replaced by "[REDACTED]".
- register_secret(value): from now on, redact() masks this exact value too.
  None, "" and values under 8 characters are ignored (masking "abc" would
  wreck every message). forget_secrets() clears them (for tests).
- RedactingFilter: a logging.Filter that redacts a record's message (with
  its arguments filled in) and its traceback.
- configure_logging(log_path=config.MONITOR_LOG_PATH, level=logging.INFO):
  sends the "hardware_monitor" loggers' records to the console and to
  log_path ("<time> <LEVEL> <logger name>: <message>"), both through a
  RedactingFilter. Calling it again does not add handlers twice.

And across the package:
- every operational module has `logger = logging.getLogger(__name__)` and
  no print(): config, dashboard, maintenance, repository, retry,
  context_managers, stats_viewer (demo.py and reports.py print the
  program's actual output, so they keep print);
- config registers the GitHub token (encrypted and decrypted), the
  encryption key and the Gemini key as secrets when it reads them;
- levels: INFO for actions (a call resolved, an issue posted), WARNING for
  problems the program works around (a retry, a skipped log line, a call
  it could not save), ERROR for failures (an alert not sent, a failed
  diagnostic session, a token that can't be decrypted);
- no log record names a technician (their tech_id is logged instead) or
  holds the computer's name;
- main.py configures logging once at startup.

Only the external dependencies are mocked; logging output is read with
pytest's caplog, or from a log file under tmp_path."""

import ast
import logging
import os
import sys
from unittest import mock

import pytest
from cryptography.fernet import Fernet

from conftest import PROJECT_ROOT
from hardware_monitor import retry as retry_module
from hardware_monitor.config import COMPUTER_NAME

PACKAGE = os.path.join(PROJECT_ROOT, "hardware_monitor")
OPERATIONAL_MODULES = ["config", "dashboard", "maintenance", "repository", "retry", "context_managers",
                       "stats_viewer"]
# each split in two, so this file holds nothing shaped like a secret (test_repository_safety)
SAMPLE_SECRETS = {
    "github-token": "ghp_" + "a1B2c3D4e5" * 4,
    "fine-grained-token": "github_pat_" + "11ABCDEFG0" * 4,
    "google-key": "AIza" + "SyD-example_Key0123456789abcdefgh",
    "fernet-token": "gAAAAAB" + "kZ3x_Example-Token0123456789" * 2,
    "fernet-key": "Zm9yLXRlc3RzLW9ubHkt" + "bm90LWEtcmVhbC1rZXkxMjM=",
}


def safe_logging():
    from hardware_monitor import safe_logging as module
    return module


@pytest.fixture(autouse=True)
def no_registered_secrets():
    """Each test starts with no registered secrets, if the module exists yet."""
    yield
    try:
        safe_logging().forget_secrets()
    except (ImportError, AttributeError):
        pass


def records_from(caplog, module):
    return [r for r in caplog.records if r.name == f"hardware_monitor.{module}"]


# --- redact() and register_secret() ---

@pytest.mark.parametrize("secret", SAMPLE_SECRETS.values(), ids=SAMPLE_SECRETS.keys())
def test_redact_masks_anything_shaped_like_a_secret(secret):
    assert safe_logging().redact(f"sent {secret} to GitHub") == "sent [REDACTED] to GitHub"


def test_redact_leaves_ordinary_text_alone():
    text = "Alert posted to GitHub for camera: issue #42 (https://github.com/owner/repo/issues/42)"
    assert safe_logging().redact(text) == text


def test_a_registered_secret_is_masked_whatever_its_shape():
    safe_logging().register_secret("plain-old-password")
    assert safe_logging().redact("login with plain-old-password failed") == "login with [REDACTED] failed"


@pytest.mark.parametrize("value", [None, "", "short"], ids=["none", "empty", "under-8-chars"])
def test_values_too_short_to_be_secrets_are_not_registered(value):
    safe_logging().register_secret(value)
    assert safe_logging().redact("a short status line") == "a short status line"


def test_redact_hides_the_home_folder_and_with_it_the_user_name():
    home = os.path.expanduser("~")
    path = os.path.join(home, "project", "logs", "x.jsonl")
    for shown in (path, repr(path)):  # an OSError quotes the path with repr()
        assert home not in safe_logging().redact(f"Could not save to {shown}")
    assert safe_logging().redact(f"at {path}") == "at " + os.path.join("~", "project", "logs", "x.jsonl")


def test_forget_secrets_clears_them():
    safe_logging().register_secret("plain-old-password")
    safe_logging().forget_secrets()
    assert safe_logging().redact("plain-old-password") == "plain-old-password"


# --- configure_logging(): the console and the log file, both redacted ---

def test_records_go_to_the_log_file_with_time_level_and_module(tmp_path):
    log_file = tmp_path / "monitor.log"
    safe_logging().configure_logging(log_path=str(log_file))
    logging.getLogger("hardware_monitor.dashboard").warning("camera alert not sent")

    (line,) = log_file.read_text(encoding="utf-8").splitlines()
    assert line.endswith("WARNING hardware_monitor.dashboard: camera alert not sent")
    assert line[:4].isdigit()  # starts with the time


def test_info_is_recorded_and_debug_is_not_by_default(tmp_path):
    log_file = tmp_path / "monitor.log"
    safe_logging().configure_logging(log_path=str(log_file))
    logging.getLogger("hardware_monitor.maintenance").info("call resolved")
    logging.getLogger("hardware_monitor.maintenance").debug("internal detail")

    assert log_file.read_text(encoding="utf-8").count("\n") == 1


def test_configuring_twice_does_not_write_each_record_twice(tmp_path):
    log_file = tmp_path / "monitor.log"
    safe_logging().configure_logging(log_path=str(log_file))
    safe_logging().configure_logging(log_path=str(log_file))
    logging.getLogger("hardware_monitor.retry").warning("once")

    assert log_file.read_text(encoding="utf-8").count("once") == 1


def test_secrets_in_messages_and_arguments_never_reach_the_file(tmp_path):
    log_file = tmp_path / "monitor.log"
    safe_logging().configure_logging(log_path=str(log_file))
    safe_logging().register_secret("plain-old-password")
    logger = logging.getLogger("hardware_monitor.config")
    logger.error(f"token {SAMPLE_SECRETS['github-token']} rejected")
    logger.error("key %s rejected", SAMPLE_SECRETS["google-key"])
    logger.error("password %s rejected", "plain-old-password")

    text = log_file.read_text(encoding="utf-8")
    assert text.count("[REDACTED]") == 3
    assert not any(s in text for s in [*SAMPLE_SECRETS.values(), "plain-old-password"])


def test_secrets_in_a_traceback_never_reach_the_file(tmp_path):
    log_file = tmp_path / "monitor.log"
    safe_logging().configure_logging(log_path=str(log_file))
    try:
        raise ValueError(f"bad token {SAMPLE_SECRETS['github-token']}")
    except ValueError:
        logging.getLogger("hardware_monitor.dashboard").exception("monitor stopped")

    text = log_file.read_text(encoding="utf-8")
    assert "Traceback" in text and "[REDACTED]" in text
    assert SAMPLE_SECRETS["github-token"] not in text


def test_the_console_is_redacted_too(tmp_path, capsys):
    safe_logging().configure_logging(log_path=str(tmp_path / "monitor.log"))
    logging.getLogger("hardware_monitor.retry").warning(f"retrying with {SAMPLE_SECRETS['fernet-key']}")

    captured = capsys.readouterr()
    console = captured.out + captured.err
    assert "retrying with [REDACTED]" in console and SAMPLE_SECRETS["fernet-key"] not in console


def test_the_default_log_file_is_in_logs_and_ignored_by_git():
    from hardware_monitor import config
    assert config.MONITOR_LOG_PATH == os.path.join(PROJECT_ROOT, "logs", "monitor.log")
    with open(os.path.join(PROJECT_ROOT, ".gitignore"), encoding="utf-8") as gitignore:
        assert "logs/*.log" in gitignore.read().splitlines()


# --- across the package: loggers instead of print ---

def module_tree(module):
    with open(os.path.join(PACKAGE, f"{module}.py"), encoding="utf-8") as source:
        return ast.parse(source.read())


@pytest.mark.parametrize("module", OPERATIONAL_MODULES)
def test_operational_modules_use_a_logger_not_print(module):
    tree = module_tree(module)
    prints = [node.lineno for node in ast.walk(tree)
              if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print"]
    assert prints == [], f"print() on lines {prints}"
    assert "logger = logging.getLogger(__name__)" in ast.unparse(tree)


def test_main_configures_logging_once_at_startup(monkeypatch):
    import main
    configure = mock.Mock()
    monkeypatch.setattr(main, "configure_logging", configure, raising=False)
    monkeypatch.setattr(main, "_run", lambda args: 0)
    monkeypatch.setattr(sys, "argv", ["main.py", "--report"])

    assert main.main() == 0
    configure.assert_called_once_with()


# --- config: the secrets it reads are registered, its problems logged ---

@pytest.fixture
def throwaway_token(monkeypatch):
    """A token encrypted with a throwaway key, in the environment as .env would put it."""
    key = Fernet.generate_key()
    token = "ghp_" + "Zz9Yy8Xx7W" * 4
    encrypted = Fernet(key).encrypt(token.encode()).decode()
    monkeypatch.setenv("GITHUB_TOKEN", encrypted)
    monkeypatch.setenv("ENCRYPTION_KEY", key.decode())
    return token, encrypted, key.decode()


def test_the_github_token_and_key_are_registered_as_secrets(throwaway_token):
    from hardware_monitor.config import get_github_token
    token, encrypted, key = throwaway_token
    assert get_github_token() == token

    for value in throwaway_token:
        assert safe_logging().redact(f"x {value} y") == "x [REDACTED] y"


def test_the_gemini_key_is_registered_as_a_secret(monkeypatch):
    from hardware_monitor.config import get_gemini_api_key
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key-without-the-usual-shape")
    get_gemini_api_key()
    assert safe_logging().redact("gemini-key-without-the-usual-shape") == "[REDACTED]"


def test_a_missing_token_is_a_warning(caplog):
    from hardware_monitor.config import get_github_token
    with caplog.at_level(logging.INFO):
        get_github_token()

    (record,) = records_from(caplog, "config")
    assert record.levelno == logging.WARNING and "GITHUB_TOKEN or ENCRYPTION_KEY not found" in record.getMessage()


def test_a_token_that_cannot_be_decrypted_is_an_error(throwaway_token, monkeypatch, caplog):
    from hardware_monitor.config import get_github_token
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())  # the wrong key
    with caplog.at_level(logging.INFO):
        assert get_github_token() is None

    (record,) = records_from(caplog, "config")
    assert record.levelno == logging.ERROR and "Decryption failed" in record.getMessage()
    assert not any(value in record.getMessage() for value in throwaway_token)


# --- the other modules' events, each at its level ---

def test_a_retry_is_a_warning(caplog, retry_waits):
    attempts = iter([TimeoutError("slow"), "done"])

    def operation():
        outcome = next(attempts)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    with caplog.at_level(logging.INFO):
        retry_module.retry(operation, retry_on=TimeoutError, max_attempts=3, backoff_seconds=1, label="GitHub")

    (record,) = records_from(caplog, "retry")
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "GitHub attempt 1/3 failed (slow) Retrying in 1 s."


def test_a_skipped_log_line_is_a_warning(tmp_path, caplog):
    from hardware_monitor.repository import iter_jsonl_records
    log = tmp_path / "log.jsonl"
    log.write_text('{"alert_id": 1}\nnot json\n', encoding="utf-8")
    with caplog.at_level(logging.INFO):
        assert list(iter_jsonl_records(str(log))) == [{"alert_id": 1}]

    (record,) = records_from(caplog, "repository")
    assert record.levelno == logging.WARNING and "Skipping corrupt line 2" in record.getMessage()


def test_a_call_that_cannot_be_saved_is_a_warning(desk, alerts, caplog, monkeypatch):
    def disk_full(path, record):
        raise OSError("No space left on device")

    monkeypatch.setattr("hardware_monitor.maintenance.append_jsonl_record", disk_full)
    with caplog.at_level(logging.INFO):
        request = desk.open_request_for_alert(alerts.camera())

    (record,) = records_from(caplog, "maintenance")
    assert record.levelno == logging.WARNING and f"Could not save call {request.request_id}" in record.getMessage()


def test_a_failed_diagnostic_session_is_an_error(caplog):
    from hardware_monitor.context_managers import DiagnosticConnection
    from hardware_monitor.models import Camera
    camera = Camera("CAM-1", "Webcam", "WS-1")
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError):
        with DiagnosticConnection(camera):
            raise RuntimeError("lens cap on")

    errors = [r for r in records_from(caplog, "context_managers") if r.levelno == logging.ERROR]
    assert [r.getMessage() for r in errors] == ["[DIAGNOSTICS] Session failed with error (RuntimeError): lens cap on"]


def test_a_posted_alert_is_info_and_an_unsent_one_an_error(dash, github, caplog):
    with caplog.at_level(logging.INFO):
        dash.app.send_github_alert("camera", "Camera Disconnected", "no frame")
        github.reply = api_error_401()
        dash.app.send_github_alert("mic_conn", "Microphone Disconnected", "unplugged")

    records = records_from(caplog, "dashboard")
    assert [(r.levelno, r.getMessage().split(":")[0]) for r in records] == [
        (logging.INFO, "Alert posted to GitHub for camera"),
        (logging.ERROR, "GitHub alert for mic_conn not sent"),
    ]


def api_error_401():
    from fake_http import api_response
    return api_response(401, {"message": "Bad credentials"})


# --- no personal information: technicians by id, never the computer's name ---

def test_a_resolved_call_names_the_technician_by_id_only(dash, caplog):
    app = dash.app
    request, _ = app.open_maintenance_call("camera", "no frame")
    technician = app.maintenance_desk.technician_for(request)
    with caplog.at_level(logging.INFO):
        app.resolve_maintenance_call("camera")

    (record,) = [r for r in records_from(caplog, "dashboard") if "resolved" in r.getMessage()]
    assert record.levelno == logging.INFO
    assert technician.tech_id in record.getMessage() and technician.name not in record.getMessage()


def test_a_whole_alert_names_no_person_and_no_computer(dash, github, caplog):
    app = dash.app
    names = [t.name for t in app.maintenance_desk.technicians.values()]
    with caplog.at_level(logging.DEBUG):
        request, _ = app.open_maintenance_call("camera", "no frame")
        app.send_github_alert("camera", "Camera Disconnected", "no frame", request)
        app.resolve_maintenance_call("camera")

    messages = [r.getMessage() for r in caplog.records if r.name.startswith("hardware_monitor")]
    assert messages  # the alert was logged
    leaks = [m for m in messages if COMPUTER_NAME in m or any(name in m for name in names)]
    assert leaks == []
