"""Decrypting the GitHub token from .env, with a throwaway key. The real
.env is never read: conftest's no_real_secrets turns load_dotenv into a no-op
and clears both variables for every test; these tests set their own."""

import dotenv
import pytest
from cryptography.fernet import Fernet

from conftest import encrypt_token
from hardware_monitor.config import get_github_token


@pytest.fixture
def env_vars(monkeypatch):
    def set_vars(token=None, key=None):
        if token is not None:
            monkeypatch.setenv("GITHUB_TOKEN", token)
        if key is not None:
            monkeypatch.setenv("ENCRYPTION_KEY", key)

    return set_vars


def test_a_valid_token_is_decrypted(env_vars):
    token, key = encrypt_token("ghp_example_token")
    env_vars(token=token, key=key)
    assert get_github_token() == "ghp_example_token"


@pytest.mark.parametrize("saved", ["ghp_example_token\n", " ghp_example_token\r\n"],
                         ids=["trailing-newline", "spaces-and-crlf"])
def test_whitespace_saved_around_the_token_is_dropped(env_vars, saved):
    # requests rejects a header with a newline in it, so the alert could never be sent
    token, key = encrypt_token(saved)
    env_vars(token=token, key=key)
    assert get_github_token() == "ghp_example_token"


def test_a_token_of_only_whitespace_gives_no_token(env_vars):
    token, key = encrypt_token(" \n")
    env_vars(token=token, key=key)
    assert not get_github_token()


@pytest.mark.parametrize("present",["token_only", "key_only", "neither"])
def test_a_missing_value_gives_no_token(env_vars, capsys, present):
    token, key = encrypt_token("ghp_example_token")
    env_vars(token=token if present == "token_only" else None, key=key if present == "key_only" else None)

    assert get_github_token() is None
    assert "GITHUB_TOKEN or ENCRYPTION_KEY not found" in capsys.readouterr().out


def test_an_empty_value_counts_as_missing(env_vars):
    env_vars(token="", key=Fernet.generate_key().decode())
    assert get_github_token() is None


@pytest.mark.parametrize("problem", ["wrong_key", "malformed_key", "tampered_token"])
def test_a_token_that_cannot_be_decrypted_gives_no_token(env_vars, capsys, problem):
    token, key = encrypt_token("ghp_example_token")
    if problem == "wrong_key":
        key = Fernet.generate_key().decode()
    elif problem == "malformed_key":
        key = "not-a-valid-key"
    else:
        token = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    env_vars(token=token, key=key)

    assert get_github_token() is None
    assert "Decryption failed" in capsys.readouterr().out


def test_load_dotenv_is_what_reads_the_file(env_vars, monkeypatch):
    token, key = encrypt_token("from-the-env-file")

    def fake_load_dotenv(*args, **kwargs):
        monkeypatch.setenv("GITHUB_TOKEN", token)
        monkeypatch.setenv("ENCRYPTION_KEY", key)

    monkeypatch.setattr(dotenv, "load_dotenv", fake_load_dotenv)
    assert get_github_token() == "from-the-env-file"
