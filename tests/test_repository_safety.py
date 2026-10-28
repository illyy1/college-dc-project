"""Repository safety: the real secrets never get into git. .env and its
variants (.env.local, ...) are ignored by .gitignore and untracked, no
tracked file holds anything shaped like a real token or key, and
.env.example lists every variable config.py reads, with sample values only.

The real .env is never opened: only git's view of it is checked."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import PROJECT_ROOT
from hardware_monitor.safe_logging import SECRET_PATTERNS  # what real secrets look like

ROOT = Path(PROJECT_ROOT)
EXAMPLE = ROOT / ".env.example"

needs_git = pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs git and the repository")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def example_variables():
    """{name: value} for every NAME=value line of .env.example."""
    lines = EXAMPLE.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if line.strip() and not line.lstrip().startswith("#"))


@needs_git
@pytest.mark.parametrize("name", [".env", ".env.local", ".env.production"],
                         ids=["env", "env-local", "env-production"])
def test_env_files_are_ignored(name):
    # --no-index: the rule itself is checked, tracked or not
    assert git("check-ignore", "-q", "--no-index", name).returncode == 0


@needs_git
def test_the_example_is_not_ignored():
    assert git("check-ignore", "-q", "--no-index", ".env.example").returncode == 1


@needs_git
def test_no_env_file_is_tracked():
    tracked = git("ls-files").stdout.splitlines()
    assert [f for f in tracked if Path(f).name.startswith(".env") and f != ".env.example"] == []


@needs_git
def test_no_tracked_file_holds_a_real_secret():
    found = git("grep", "-n", "-I", "-E", "|".join(f"({p})" for p in SECRET_PATTERNS)).stdout
    # only where, never the value: a test failure is printed, and CI logs are public
    assert [line.split(":", 2)[:2] for line in found.splitlines()] == []


def test_the_example_lists_every_variable_config_reads():
    source = (ROOT / "hardware_monitor" / "config.py").read_text(encoding="utf-8")
    read = set(re.findall(r"""os\.getenv\(\s*["']([A-Z_]+)["']""", source))
    assert read == {"GITHUB_TOKEN", "ENCRYPTION_KEY", "GEMINI_API_KEY"}
    assert set(example_variables()) == read


def test_the_example_holds_sample_values_only():
    for name, value in example_variables().items():
        assert value.startswith("<") and value.endswith(">"), f"{name} should be a <placeholder>"
        assert not any(re.search(p, value) for p in SECRET_PATTERNS), f"{name} looks like a real secret"
