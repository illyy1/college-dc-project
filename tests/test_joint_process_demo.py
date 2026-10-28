"""TDD specification for the demo's joint process (Part 2, sections 6.5 and
7.1): a fault in the monitor reaches the maintenance subsystem through the
external services, and the change shows in the report.

What hardware_monitor/demo.py must provide:
- SimulatedServices(gemini_fails=False): offline stand-ins for the three
  API calls. find_open_issues finds nothing open; triage_fault returns a
  triage of priority 2, cause "cable", 15 minutes (or, with gemini_fails,
  the failure "Gemini did not answer within 10 s."); create_issue posts
  issue #1, #2, ... (keeping their titles in .issues), with links that are
  clearly simulated. clients() gives them as
  report_fault's create_issue / find_open_issues / triage_fault.
- demo_joint_process(label="SIMULATED", services=None, failing_services=None,
  github_token=..., gemini_api_key=...) -> (triaged_call, untriaged_call)
  On a MaintenanceDesk over DEMO_MAINTENANCE_LOG_PATH: a microphone
  disconnect (rule priority 3) opens a call, which goes to
  services.report_fault() with the injected clients; Gemini's triage
  changes it. A second disconnect goes through with Gemini failing, so its
  call keeps priority 3. It prints what triggered each, what was sent, what
  changed ("priority 3 -> 2"), the issue, the failure's reason, and the
  report's triage figures (services.triage_summary of the demo log). Every
  line it prints starts with [<label>]. By default it uses
  SimulatedServices, so it runs offline with no keys.
- run_demo() runs it as "STEP 12 — Joint process".

And main.py gets a --live flag (the same step against the real services,
labelled [REAL]). It is only parsed here: --live never runs in the tests."""

import sys

import pytest

from hardware_monitor import api_client, async_processing, demo, services
from hardware_monitor.repository import load_maintenance_history


@pytest.fixture
def demo_log(tmp_path, monkeypatch):
    """The demo maintenance log, under tmp_path."""
    path = str(tmp_path / "demo_maintenance.jsonl")
    monkeypatch.setattr("hardware_monitor.demo.DEMO_MAINTENANCE_LOG_PATH", path)
    return path


@pytest.fixture
def joint(demo_log, capsys):
    """Runs the offline joint process once: its two calls, what it printed,
    and the calls as saved in the demo log (their last state)."""
    triaged, untriaged = demo.demo_joint_process()
    output = capsys.readouterr().out
    saved = {call.request_id: call for call in load_maintenance_history(demo_log)}

    class Run:
        pass

    run = Run()
    run.triaged, run.untriaged, run.output, run.saved = triaged, untriaged, output, saved
    return run


# --- the joint process succeeds: Gemini's triage changes the call ---

def test_the_triage_raises_the_call_from_3_to_2(joint):
    call = joint.saved[joint.triaged.request_id]  # as saved, not just as printed
    assert (call.priority, call.cause, call.estimated_minutes) == (2, "cable", 15)
    assert call.error_type == "MICROPHONE_DISCONNECTED"


def test_the_output_shows_the_trigger_the_data_and_the_change(joint):
    out = joint.output
    assert "MICROPHONE_DISCONNECTED" in out and "rule priority 3" in out  # what triggered it
    assert joint.triaged.request_id in out                               # what was passed on
    assert "priority 3 -> 2" in out                                      # what changed
    assert "issue #1" in out


def test_the_report_figures_include_the_triaged_call(joint, demo_log):
    summary = services.triage_summary(load_maintenance_history(demo_log))
    assert summary.triaged_calls == 1 and summary.causes == [("cable", 1)]
    assert summary.open_backlog_minutes == 15

    out = joint.output
    assert "cable: 1" in out and "open backlog: 15 min" in out


# --- the external service fails: the call is unchanged, the issue still goes out ---

def test_when_gemini_fails_the_call_keeps_priority_3(joint):
    call = joint.saved[joint.untriaged.request_id]
    assert (call.priority, call.cause, call.estimated_minutes) == (3, None, None)


def test_the_failure_s_reason_is_printed_and_the_issue_still_posted(joint):
    out = joint.output
    assert "Gemini did not answer within 10 s." in out
    assert "keeps priority 3" in out
    assert "issue #2" in out


# --- simulated, offline, through the real report_fault ---

def test_every_line_says_simulated(joint):
    lines = [line.strip() for line in joint.output.splitlines() if line.strip()]
    assert lines and all(line.startswith("[SIMULATED]") for line in lines)
    assert "[REAL]" not in joint.output


def test_nothing_reaches_the_real_services(demo_log, github, gemini):
    demo.demo_joint_process()
    assert github.calls == [] and github.search.calls == [] and gemini.calls == []


def test_the_real_report_fault_gets_the_calls_and_the_simulated_clients(demo_log, monkeypatch):
    seen = []
    real = services.report_fault

    def spy(*args, **kwargs):
        seen.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(services, "report_fault", spy)
    triaged, untriaged = demo.demo_joint_process()

    assert [args[6].request_id for args, _ in seen] == [triaged.request_id, untriaged.request_id]
    for _, clients in seen:
        assert clients["triage_fault"] is not api_client.triage_fault
        assert clients["create_issue"] is not api_client.create_issue
        assert clients["find_open_issues"] is not api_client.find_open_issues


def test_simulated_services_post_numbered_issues_and_fail_on_request():
    working, failing = demo.SimulatedServices(), demo.SimulatedServices(gemini_fails=True)
    assert working.triage_fault("key", "t", "d", "c").triage.priority == 2
    result = failing.triage_fault("key", "t", "d", "c")
    assert not result.ok and result.error == "Gemini did not answer within 10 s."
    assert [working.create_issue("token", "o/r", "t", "b").issue_number for _ in range(2)] == [1, 2]
    assert working.find_open_issues("token", "o/r", ["hardware-alert"]).issues == ()


# --- in the demo, and the --live flag ---

def test_run_demo_includes_the_joint_process(demo_log, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("hardware_monitor.demo.DEMO_LOG_PATH", str(tmp_path / "demo_history.jsonl"))
    monkeypatch.setattr(async_processing, "SOURCE_LATENCY_SECONDS", 0)
    demo.run_demo()
    out = capsys.readouterr().out
    assert "STEP 12 — Joint process" in out and "[SIMULATED]" in out


@pytest.mark.parametrize("flags, live", [(["--live"], True), (["--no-dashboard"], False), ([], False)],
                         ids=["live", "no-dashboard", "no-flags"])
def test_live_is_parsed_but_never_run_here(monkeypatch, flags, live):
    import main
    received = []
    monkeypatch.setattr(main, "_run", lambda args: received.append(args) or 0)  # nothing after parsing runs
    monkeypatch.setattr(sys, "argv", ["main.py", *flags])

    assert main.main() == 0
    assert received[0].live is live
