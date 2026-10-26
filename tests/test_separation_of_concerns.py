"""Separation of concerns (TDD specification): raw network communication
lives only in hardware_monitor/api_client.py; the business logic and the
decisions made with its data live in hardware_monitor/services.py.

- api_client.py sends the requests (GitHub Issues, Gemini), checks the
  replies (status, JSON, every field) and turns them into plain results
  (IssueResult, TriageResult / Triage). It knows nothing about calls,
  technicians, the desk or the dashboard: from the package it imports only
  the generic retry loop. It replaces github_client.py, gemini_client.py
  and api_calls.py.
- services.py decides what to do with that data: whether to ask Gemini, the
  call's final priority (the more urgent of the hardware rule's and
  Gemini's), the GitHub issue's text and labels, and the calculations from
  the triages (triage_summary). It never touches the network itself; it
  goes through api_client.
- The dashboard asks services, never api_client; no other module talks to
  the network. The desk only stores: update_request() changes a call under
  its lock and saves it, whatever the change is.

The structure is read from the source (ast), so the rules hold for code
the tests never run."""

import ast
import importlib.util
import os

import pytest
import requests

from fake_gemini import TRIAGE, api_error
from hardware_monitor import services
from hardware_monitor.api_client import IssueResult, Triage
from hardware_monitor.repository import load_maintenance_history

from conftest import GEMINI_KEY, PROJECT_ROOT

PACKAGE = os.path.join(PROJECT_ROOT, "hardware_monitor")
# libraries that talk to the network
NETWORK_LIBRARIES = {"requests", "urllib", "urllib3", "http", "socket", "httpx", "aiohttp"}
# config reads the machine's name with socket.gethostname(): no network traffic
LOCAL_SOCKET_USE = {"config"}


def package_modules():
    return sorted(name[:-3] for name in os.listdir(PACKAGE) if name.endswith(".py"))


def imports_of(module):
    """(the outside libraries, the sibling modules of the package) a module
    imports, anywhere in its source (inside functions too)."""
    with open(os.path.join(PACKAGE, f"{module}.py"), encoding="utf-8") as source:
        tree = ast.parse(source.read())
    libraries, siblings = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            libraries |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level:  # from .x import y / from . import x
            siblings |= {node.module.split(".")[0]} if node.module else {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            libraries.add(node.module.split(".")[0])
    return libraries, siblings


# --- where the network code is ---

@pytest.mark.parametrize("old_module", ["github_client", "gemini_client", "api_calls"])
def test_the_old_api_modules_are_gone(old_module):
    # one place for the network code, not three
    assert importlib.util.find_spec(f"hardware_monitor.{old_module}") is None


def test_the_api_client_talks_to_the_network():
    libraries, _ = imports_of("api_client")
    assert "requests" in libraries


@pytest.mark.parametrize("module", [m for m in package_modules() if m != "api_client"])
def test_no_other_module_talks_to_the_network(module):
    libraries, _ = imports_of(module)
    allowed = {"socket"} if module in LOCAL_SOCKET_USE else set()
    assert libraries & NETWORK_LIBRARIES <= allowed


def test_the_api_client_knows_nothing_of_the_business():
    # no calls, technicians, desk, reports or dashboard: only the generic retry loop
    _, siblings = imports_of("api_client")
    assert siblings <= {"retry"}


# --- where the decisions are ---

def test_the_services_use_the_api_client_and_never_the_network_directly():
    libraries, siblings = imports_of("services")
    assert "api_client" in siblings and not libraries & NETWORK_LIBRARIES


def test_the_dashboard_goes_through_the_services():
    _, siblings = imports_of("dashboard")
    assert "services" in siblings and "api_client" not in siblings


@pytest.mark.parametrize("module", ["maintenance", "reports", "models", "stats"])
def test_the_business_modules_never_call_the_api_client(module):
    _, siblings = imports_of(module)
    assert "api_client" not in siblings


# --- services.report_fault: the decisions, in one place ---

def fault_call(desk, alerts, priority=4):
    return desk.open_request_for_alert(alerts.camera(component_id="CAM-LOCAL", priority=priority))


def report(desk, request, gemini_api_key=GEMINI_KEY):
    return services.report_fault(desk, "test-token", gemini_api_key, "camera", "Camera Disconnected",
                                 "no frame", request)


def test_with_a_key_the_triage_decides_the_priority_and_labels(desk, alerts, github, gemini):
    request = fault_call(desk, alerts, priority=4)
    outcome = report(desk, request)

    assert outcome.triage == Triage(**{**TRIAGE, "steps": tuple(TRIAGE["steps"])})
    assert outcome.priority == request.priority == TRIAGE["priority"]   # more urgent than the rule's 4
    assert outcome.issue == IssueResult(True, issue_number=1, url="https://github.com/owner/repo/issues/1")
    assert github.calls[0].json["labels"] == ["hardware-alert", "camera", f"priority-{TRIAGE['priority']}"]


def test_without_a_key_gemini_is_not_asked_and_nothing_is_decided(desk, alerts, github, gemini):
    request = fault_call(desk, alerts, priority=4)
    outcome = report(desk, request, gemini_api_key=None)

    assert gemini.calls == [] and outcome.triage is None and outcome.triage_error is None
    assert outcome.priority is None and request.priority == 4
    assert outcome.issue.ok and github.calls[0].json["labels"] == ["hardware-alert", "camera"]


@pytest.mark.parametrize("failure, reason", [
    (api_error(400, "API key not valid."), "HTTP 400"),
    (requests.exceptions.ReadTimeout(), "did not answer"),
], ids=["bad-key", "timeout"])
def test_when_gemini_fails_the_issue_still_goes_and_says_why(desk, alerts, github, gemini, failure, reason):
    gemini.reply = failure
    request = fault_call(desk, alerts, priority=4)
    outcome = report(desk, request)

    assert outcome.triage is None and reason in outcome.triage_error
    assert request.priority == 4 and outcome.issue.ok


def test_the_issue_text_is_composed_by_the_services(desk, alerts, github, gemini):
    request = fault_call(desk, alerts)
    report(desk, request)

    body = github.posted[0].body
    assert body.startswith("### Hardware Alert Triggered") and f"`{request.request_id}`" in body
    assert "AI-generated by Gemini" in body and TRIAGE["steps"][0] in body


# --- the desk only stores ---

def test_the_desk_updates_and_saves_a_call_whatever_the_change(desk, alerts):
    request = desk.open_request_for_alert(alerts.camera())

    def note(call):
        call.cause = "noted"

    assert desk.update_request(request.request_id, note) is request
    assert request.cause == "noted"
    assert load_maintenance_history(desk.log_path)[-1].cause == "noted"


def test_updating_an_unknown_call_raises(desk):
    with pytest.raises(ValueError, match="Maintenance request 'MR-9999' does not exist"):
        desk.update_request("MR-9999", lambda call: None)
