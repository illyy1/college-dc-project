# Room Hardware Monitor (IT Asset & Alert System)

## הצעת הפרויקט (Project Proposal)
*   **Name and description:** Room Hardware Monitor, an IT information system that tracks the cameras and microphones in meeting rooms and workstations, raises alerts when they fail, and sends a technician to fix them.
*   **Business need:** Meeting rooms often discover a dead camera or microphone only when a meeting starts. Faults are reported late, by phone, and nobody knows which room is affected or who is handling it.
*   **Users and roles:**
    *   *IT help-desk administrator:* manages the hardware inventory and acknowledges every incoming alert.
    *   *Technician:* receives maintenance calls and marks them resolved. There are no specialists: every technician can take any call.
    *   *Automated monitor (system actor):* runs diagnostics on each component and creates alerts.
*   **Main business process:** A diagnostic check or the live monitor detects a failed component. An alert is created, the help desk acknowledges it in arrival order, and a maintenance call is opened and assigned by a random draw between all technicians, weighted towards those with fewer calls so far, most urgent fault first. The process ends when the technician resolves the call and the component is back to `ACTIVE`.
*   **Information flow:** In: the inventory of workstations and components (`data/sample_data.jsonl`), the technician list (`data/technicians.jsonl`) and live diagnostic results. The monitor creates alerts; the maintenance desk creates and updates maintenance calls. Out: an alert history log, a maintenance call history, a technician workload report and a CSV report for management.
*   **Business value:** Faults are found before the meeting, urgent ones are fixed first, and work is spread evenly across technicians. This cuts room downtime and makes it clear who owns each fault.
*   **Entities and relationships:**
    *   A *Workstation* contains many *HardwareComponents* (composition). A component is either a *Camera* or a *Microphone* (inheritance).
    *   A failed component produces one or more *Alerts*.
    *   Each alert opens one *MaintenanceRequest*, which is assigned to one *Technician*. A technician handles many requests.
*   **Use cases:**
    1. A room's camera disconnects and its microphone has a bad setting. The system dispatches the technician to the camera first, because a lost camera outranks a misconfigured microphone.
    2. An administrator wants the IDs of the first two old (3+ years) disconnected components, to decide what to replace, without scanning the whole fleet.
*   **Future expansion:** Alert when a monitor disconnects, when the system is muted, or when the wrong default microphone is selected. Swap the synthetic signals for real hardware input (`sounddevice`/`numpy` for the microphone, `opencv-python` for the camera).

## Requirements
- Python **3.10 or newer** (the code uses `X | None` type hints).
- Tkinter for the live dashboard (included with the standard Python installer on Windows and macOS).
- The demo (`python main.py --no-dashboard`) needs only the standard library. The packages in `requirements.txt` are used by the live dashboard: `requests` opens GitHub issues, and `python-dotenv` + `cryptography` read the encrypted token from `.env`.

## Installation
```
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```
Alternatively, install the project as a package with `pip install -e .`. `pyproject.toml` describes the project as an installable Python package: its name (`room-hardware-monitor`), version, minimum Python version, dependencies and the `hardware_monitor` package. The `-e` (editable) flag means code changes take effect without reinstalling. `python main.py` works the same either way.

`requirements.txt` and `pyproject.toml` list the same dependencies. If you add or remove one, update both files.

To build a standalone executable, also install PyInstaller: `pip install pyinstaller`

### GitHub alerts (optional)
The live dashboard can open a GitHub issue for each new fault. This needs a `.env` file in the project root (it is in `.gitignore` and is never committed). Copy `.env.example` to `.env` and fill it in; it lists every value, the GitHub ones and the Gemini key:
```
GITHUB_TOKEN=<GitHub personal access token, encrypted with Fernet>
ENCRYPTION_KEY=<the Fernet key used to encrypt it>
```
To create the two values:
```
python -c "from cryptography.fernet import Fernet; k = Fernet.generate_key(); print('ENCRYPTION_KEY=' + k.decode()); print('GITHUB_TOKEN=' + Fernet(k).encrypt(b'<your token>').decode())"
```
Without `.env`, everything still runs: the dashboard prints a warning and shows alerts and maintenance calls on screen, but posts nothing to GitHub.

### AI triage (optional)
With a Gemini API key from Google AI Studio in the same `.env`, Gemini (`gemini-3.1-flash-lite`) triages each new fault: a priority (1 = most urgent), the likely cause (cable, driver, hardware, permissions, software or other), an estimated repair time and up to five troubleshooting steps, as checked JSON. The triage drives the system:
- **The call's priority:** it takes the more urgent of the hardware rule's and Gemini's, so Gemini can raise urgency but never lower it. The cause and the estimate are saved with the call, in the log and the CSV report.
- **The GitHub issue:** labelled `priority-N` with the call's final priority, with a Triage section marked as AI-generated.
- **The history report (`--report`):** the most common causes, the estimated repair time per technician, and the estimated work still open.

```
GEMINI_API_KEY=<your key from https://aistudio.google.com>
```
Only the alert itself is sent (its title, component and details), never the computer's name or the technician. Without the key, when Gemini is slow or fails, or when its answer is invalid, the call keeps its priority, the issue is posted without the section, and the reason is printed.

## Running the project
| Command | What it does |
|---|---|
| `python main.py` | Runs the step-by-step demonstration, then opens the live monitoring dashboard with the live statistics window next to it (see `--stats`). When the dashboard closes, the statistics window closes too, and the maintenance history report is printed and exported. |
| `python main.py --no-stats` | The same, without the statistics window. |
| `python main.py --no-dashboard` | Runs the demonstration and the history report from start to finish without opening a window. |
| `python main.py --report` | Only prints the maintenance history report and exports it to `logs/maintenance_report.csv`. |
| `python main.py --stats` | Opens only the live alert statistics window. It re-reads the maintenance log every 2 seconds, so it follows a dashboard running at the same time, even in another terminal. It shows:<ul><li>a summary;</li><li>a pie chart of each technician's share of the calls, and one of each alert type's share;</li><li>alerts by type (camera, microphone, other);</li><li>each technician's calls, open and resolved, with their share and a bar;</li><li>the latest calls and who took each.</li></ul> |

The dashboard drives its microphone/camera alerts off synthetic signals (`hardware_monitor/synthetic_sources.py`) rather than real hardware, so alert behaviour can be tested on demand.

## Testing (regression suite)
Run `pip install -r requirements.txt pytest`, then `pytest` from the project root. The tests never contact GitHub.

**Files (`tmp_path`).** Every test that writes files (logs, CSV exports, bad or half-written data files) does it in pytest's built-in `tmp_path`, a fresh temporary folder per test. The real `data/` files are only ever read. This happens in three ways:
- 41 tests request `tmp_path` directly;
- the shared fixtures (`desk`, `make_desk`, `alert_manager`) put their logs there;
- an automatic fixture sends the alert history log there in every test.

A check in `conftest.py` enforces this. It records every project file before the run and fails the run if any test created, changed or deleted one, naming the file.

**Shared fixtures and stand-ins** (`tests/conftest.py`, `tests/fake_*.py`). Every test uses the same object for the same purpose instead of building its own. Fixtures are rebuilt for every test, so one test's changes never leak into another.
- **Test data the tests own:** the data files change as staff are hired and the fleet changes, so tests needing exact numbers never read `data/`.
  - `technicians`: a fixed four-person test team. `make_team(n)` builds a team of any size, and `technicians_file` writes the team to a temporary file.
  - `workstations`: the frozen test fleet in `tests/data/sample_fleet.jsonl`.
  - `make_call(...)`: one maintenance-log record. `history` is a seven-call log, and `reports_log` is a saved log for the report tests.
  - `BAD_FIELDS`: seven fields of the wrong type a damaged log could hold, such as an open time that is a number; `make_bad_call(...)` puts one in a saved call.
- **The real files:** `real_technicians` and `real_workstations` load `data/`, and their tests check only what any valid file must hold.
- **Mock objects:**
  - `alerts.camera()`, `alerts.microphone()` and `alerts.make(...)` build fresh alerts.
  - `bench_workstation` is a fake workstation. `alert_manager`, `make_desk` and `desk` work on temporary logs.
  - `write_jsonl(...)` writes any JSONL file, including a broken or half-written line.
- **External dependencies:**
  - `github` (`fake_github.py`) is the mocked `requests.post`, and `gemini` (`fake_gemini.py`) the mocked Gemini request; both share `fake_http.py`, so one test can fake both services.
  - `dash` is a real dashboard built by its own constructor, with Tk (`fake_tk.py`), the clock, threads and signals (`fake_dashboard.py`) faked.
  - `fake_random.LikeliestPick` stands in for `random`: it records each weighted draw and takes the likeliest technician.
- **Helpers:** `encrypt_token()` makes a throwaway encrypted token, `run_together()` starts threads at the same moment, and `slow_requests()` widens a race window.
- **Settings:** tests read every setting from the config. One test per module pins the documented defaults, so changing a setting breaks only that test.

**Isolation (mocking).** The tests run without an internet connection, without an API key, and without real waiting between retries. The rule: only external dependencies are mocked, never the function under test.
- **Applied to every test** (`tests/conftest.py`): connecting to any non-local address fails as if the machine were offline. `load_dotenv` and the token variables are cleared, so the real `.env` is never read. The `sleep` between retries (GitHub, log and report writes) is a `unittest.mock` that records the waits instead of sleeping.
- **`requests.post`** is patched with `mock.patch(..., autospec=True)`, so a call the real function would reject fails the test. Its replies are `Mock(spec=requests.Response)`. `create_issue`, the retry loop and the dashboard's posting code all run for real against it.
- **Tk, the clock and the mic/camera signals** are replaced in the dashboard tests. The token is encrypted with a throwaway key and decrypted by the real `get_github_token`.
- **Wrappers around project code** (to slow a critical section or record when something runs) always call the real function.
- `tests/test_isolation.py` proves these guarantees:
  - a real request fails as if offline;
  - no token can be loaded;
  - exhausting 6 attempts (31 s of real waits) takes no real time;
  - the function under test is the real one;
  - the `requests` mock rejects a call the real `requests.post` would reject.

The suite guarantees that new code doesn't break the Phase 1 logic:
- `tests/test_models.py`, `tests/test_processing.py`, `tests/test_maintenance.py` — the models and validation, alert queues and data structures, and the maintenance desk.
- `tests/test_phase1_regression.py` — every other Phase 1 behaviour: the custom iterator, the generator, the lazy pipeline (including stopping at the limit), `DiagnosticConnection`, the abstract `HardwareComponent` and its `from_dict` factory, loader validation, the reports, and a run of the whole demo (all 11 steps).
- `tests/test_business_logic_edge_cases.py` — business logic and edge cases:
  - **Boundaries:** the 8000 Hz sample-rate threshold, the 3-year age cut-off, the smallest valid resolution, a limit of 0.
  - **Empty inputs:** empty files, fleets and alert queues.
  - **Invalid inputs:** wrong types, blank ids, impossible dates, records that aren't JSON objects, foreign or duplicate components.
  - **Maintenance rules:** a workstation in no technician's list still gets a technician, two technicians share open calls evenly, open calls survive a restart.
  - **Phase 2 concurrency:** threads never lose an alert record, batches stay together, and the async overview matches the sequential one.
- `tests/test_design_patterns.py` — the two design patterns (see Design patterns):
  - **Factory:** picks the right product, gives each product only its own fields and validation, builds the same object as direct construction, and picks up a new subclass with no changes to the factory.
  - **Strategy:** each strategy's rule on its own, the cases where they disagree, the default, swapping on a running desk, and a custom strategy receiving only eligible technicians.
- `tests/test_external_api.py` — the GitHub Issues API, with `requests.post` replaced by a fake so no test goes over the network:
  - **Success:** the issue number and link are returned, and the request has the right URL, payload, token and timeout.
  - **HTTP errors:** 401, 403, 404, 410, 422, 5xx and an unexpected 200, each reported with its status and GitHub's message when there is one.
  - **Timeouts and network failures:** `Timeout`, `ConnectionError` and any other `RequestException` are each caught explicitly and reported as results with their own message, never raised, so a failed alert can't crash a monitoring thread. A `ConnectTimeout` counts as a timeout, and an error from outside `requests` (a bug) is not hidden. Messages name the exception but never quote it, since `requests` copies the headers or URL, and with them the key, into its messages.
  - **Incomplete replies:** missing or wrongly typed `number`/`html_url`, a body that isn't JSON, or JSON that isn't an object.
  - **The dashboard:** it posts the maintenance call and technician, and prints each failure instead of crashing.
  - **Retry mechanism:** recovery after a temporary failure (timeout, connection error, a reply cut off mid-way, 429, 5xx), including on the last allowed attempt. Giving up after the maximum attempts: no extra request, no wait after the final attempt, and the last error reported. Permanent errors (401, 404, 422, …, a bad URL or header, a redirect loop) and incomplete replies are never retried.
- `tests/test_gemini_api.py` — the Gemini triage (`api_client.triage_fault()`), written test-first (TDD, the `feature/ai-issue-text` and `feature/ai-triage` branches), with Gemini's request faked:
  - **The client:** the request (JSON mode with the triage schema; the key in a header, never the URL), every triage field checked (a missing or impossible value rejects the triage), blocked, unfinished or empty answers, HTTP errors with Gemini's message, timeouts, and retrying only temporary failures.
  - **In the dashboard:** the triage in the GitHub issue under an AI-generated heading; the issue still posted when Gemini fails or there is no key; nothing identifying a person sent to Gemini; one request per issue.
- `tests/test_duplicate_check.py` — the duplicate check, written test-first (TDD, the `feature/duplicate-check` branch), with `requests.get` faked: the GET's URL, labels and headers; the retry (a `Timeout` or `ConnectionError` recovered on the 2nd or 3rd attempt, giving up after 3 with waits of 1 s and 2 s, permanent errors not retried); every unusable reply failing with its category; and `report_fault()` posting nothing for an issue still open, posting for another computer's or another fault's issue, and posting anyway when the lookup fails, as the dashboard reports.
- `tests/test_error_categories.py` — error categorization, written test-first (TDD, the `feature/error-categories` branch): every failure path of both APIs, one parametrized row each, gets its category (communication, HTTP, unparsable JSON, failed structural validation); the category survives the retries; successes and calls with no token or key have none.
- `tests/test_business_application.py` — the triage driving the system: the call taking the more urgent priority, the cause and estimate saved (log, restart, CSV), the issue's `priority-N` label, nothing changed when Gemini fails, and the report's calculations (common causes, minutes per technician, open backlog).
- `tests/test_safe_logging.py` — safe logging, written test-first (TDD, the `feature/safe-logging` branch): the masking of secrets, registered values and the home folder (in messages, arguments and tracebacks, in the file and on the console); the log file's format; loggers instead of `print()` in the operational modules; each event at its level; no technician's or computer's name in any record. The other tests read the log with the `logged` fixture.
- `tests/test_repository_safety.py` — no secret gets into git: `.env` and its variants are ignored and untracked, no tracked file holds anything shaped like a real token or key (failures name the file and line, never the value), and `.env.example` lists exactly the variables `config.py` reads, with `<placeholder>` values only. The real `.env` is never opened.
- `tests/test_separation_of_concerns.py` — the split between network and decisions, read from the source: only `api_client.py` talks to the network and it knows nothing of the business; `services.py` never touches the network; the dashboard goes through `services.py`; and `services.report_fault()`'s decisions (with a key, without one, when Gemini fails).
- `tests/test_dashboard.py` — the live dashboard's logic, run without a window. Tk is replaced by a fake, time by a clock the tests move forward exactly, the mic/camera by scripted signals, and GitHub by a stub.
  - **Monitoring loops:** mic disconnects and silence, and camera disconnects and frame errors, each opening and then resolving a maintenance call.
  - **Timing rules, tested exactly at their limits:** silence must last *more* than 8 s; a volume exactly at the threshold isn't silence; a popup stays at least 5 s; the same fault can't pop up again within 10 s; at most one GitHub issue per fault every 10 minutes; the watchdog forces a fault after exactly 45 quiet seconds, only on a device free to show it, and never counts a break as quiet time.
  - **Pause/Resume, shutdown, and calls left open by a previous run:** pausing hides popups but keeps calls open; closing the window leaves no errors; leftover calls are adopted, with duplicates closed.
- `tests/test_synthetic_sources.py` — the simulated mic and camera: how a fault starts and clears (exact chance limits), forced faults always being seen, the volume ranges, and that the default settings give a busy room whose faults clear on their own.
- `tests/test_config.py` — decrypting the GitHub token with a throwaway key (the real `.env` is never read): a valid token, missing or empty values, a wrong or malformed key, and a tampered token.
- `tests/test_exceptions.py` — exceptions for bad data and critical failures (`pytest.raises`), and proof that a failure never leaves things half-done:
  - **Ports and locks released:** a diagnostic session that fails to start releases its port, and a failure inside one is re-raised after cleanup. A crashing assignment strategy or an unwritable log leaves no lock held.
  - **Data files:** a missing data file stops loading. A bad line raises `DataFileError` (a `ValueError`) naming the file and line, with the parser's internal error hidden.
  - **Programming errors:** an unknown package attribute raises `AttributeError`, and the abstract base methods refuse to run.

  Across the suite, 49 `pytest.raises` checks verify both the exception type and its message, except where only the type matters or the message comes from the operating system.
- `tests/test_stats.py` and `tests/test_stats_viewer.py` — the live statistics viewer, written test-first (TDD): the failing tests were committed before any of the code (the `RED:` commits on the `feature/live-stats` branch), then the code that makes them pass (`GREEN:`). They cover:
  - **The numbers:** totals; alerts by type; each technician's spread, including idle, unassigned and removed technicians; and who took each recent call.
  - **The text sections and the two pie charts:** slices filling exactly 360°, a single slice drawn as a circle, and legends with percentages.
  - **The live window:** new and resolved calls appear on the next refresh, an unreadable log keeps the last numbers, and closing stops the refreshes.
  - **A saved call with a wrongly typed field** (`feature/bad-log-records`, also TDD): the statistics skip it with one warning, the window keeps refreshing, and the desk still starts and never reuses its number.
  - **`--stats`.**
- `tests/test_failure_recovery.py` — real-world failures the system must survive. Each failure is caused at the external dependency, and the code that handles it runs for real:
  - **The CSV report locked by Excel:** the call is still opened or resolved and saved, a warning is printed, and the CSV catches up on the next change.
  - **A diagnostic that crashes:** the component is marked `ERROR` and alerted, and the sweep goes on.
  - **A log line half-written when the program was killed:** the next record starts on a fresh line, and the desk keeps numbering correctly.
  - **`--report` end to end:** it prints the history and exports the CSV, creating the folder if needed. Unlike the automatic refresh, it fails loudly if the CSV can't be written.
  - **A file held for a moment by another program** (antivirus, OneDrive, the search indexer): reading a data file or log, a log or report write, and resetting a demo log are retried; a full disk is not.
  - **A maintenance log that can't be written:** the call is still opened and kept in the run, and the log catches up on the call's next change. Any CSV write error, not only Excel's lock, leaves the call alone.
  - **The live dashboard keeps monitoring:** a fault still gets its call when the alert log can't be written, and a monitoring loop that crashes is reported and restarted.
  - **`main.py` explains instead of a traceback:** a report it can't write, or a data file or log that is missing, broken, unreadable or still locked, gives a one-line message and exit code 1. A bug is still raised, and a dashboard crash is not hidden by a failed report.
- `tests/test_retry.py` — the one retry mechanism the GitHub client, the log writes and the report share: recovery with a doubling wait, giving up with the last error, only the listed errors retried, and the attempt limit checked.
- `tests/test_concurrency.py` — concurrency validations. These tests are deterministic: they hold a lock and check that callers wait for it, or slow down a critical section so that code without the lock would fail every time.
  - **Async accuracy:** results match the data counted independently. Results stay in input order even when the tasks finish in reverse order, and repeated runs give identical results.
  - **Really concurrent:** all three sources start before any finishes, and all 8 workstation fetches are in flight at once (the sequential versions run one at a time). Blocking file reads don't freeze the event loop, and a failing source leaves no unhandled task errors.
  - **Locks taken:** every method of `MaintenanceDesk`, `AlertManager`, `LockedAlertLog` and the dashboard that touches shared state waits while its lock is held.
  - **Shared state stays consistent:** batches stay together, and a control run without the lock shows them getting mixed up. Snapshots never see half a batch, and concurrent calls and resolutions keep the numbering, workloads and log consistent. A fault reported by 8 threads opens one call, popup slots are never shared or overbooked, and only one GitHub issue opens per window. Mixed dashboard operations finish without deadlock.

GitHub Actions (`.github/workflows/tests.yml`) runs the suite on every pull request and every push to `main`, so a change that breaks old behaviour is caught before it is merged.

## Project layout
The course template's `project_name/` package is named `hardware_monitor/` here.
- `main.py` — entry point only: reads the command-line flags and calls the package. No business classes. Exits with 1 and a one-line message for a file problem: a report it can't write, or a data file or log that is missing, broken, unreadable or still locked.
- `hardware_monitor/__init__.py` — package definition and public API.
- `hardware_monitor/models.py` — `HardwareComponent`, `Camera`, `Microphone`, `Workstation`, `Technician`, `MaintenanceRequest` (OOP, validation, `from_dict` factories).
- `hardware_monitor/repository.py` — loads `data/*.jsonl` line by line into objects and validates them (a bad line raises `DataFileError`), and reads and appends to the logs, retrying a file another program holds for a moment.
- `hardware_monitor/processing.py` — `Alert`, `AlertManager` (list, dict, set, deque, heapq, comprehensions, sorting) and the polymorphic diagnostics sweep.
- `hardware_monitor/iterators.py` — `AlertHistory`/`ActiveAlertIterator`, a generator and the 3-step lazy pipeline.
- `hardware_monitor/context_managers.py` — `DiagnosticConnection`.
- `hardware_monitor/demo.py` — the step-by-step demonstration that `main.py` runs, one function per requirement.
- `hardware_monitor/maintenance.py` — `MaintenanceDesk`: opens a maintenance call for each alert, assigns it to a technician and saves every change.
- `hardware_monitor/reports.py` — technician workload report, maintenance history report (with the triage figures from `services.triage_summary()`), CSV export, cross-file technician validation.
- `hardware_monitor/concurrency.py` — async information gathering (`gather_overview_concurrent` starts three independent sources — fleet status, technician roster, maintenance history — with `asyncio.create_task` and collects them with `asyncio.gather`; demo step 11 times it against awaiting them one by one) and a lock-protected alert log for threads.
- `hardware_monitor/dashboard.py` — the live Tkinter UI, monitoring threads and GitHub alerting. A monitoring loop that crashes is restarted after 3 s.
- `hardware_monitor/stats.py` — `compute_stats()` / `load_stats()`: the numbers behind the statistics window, computed from the maintenance log. Covers totals, alerts by type, the per-technician spread and the recent calls.
- `hardware_monitor/stats_viewer.py` — the live statistics window (`python main.py --stats`): text sections, two pie charts drawn on Tk canvases, and a refresh every 2 seconds.
- `hardware_monitor/safe_logging.py` — safe logging. Every module logs through `logging.getLogger(__name__)` (INFO for actions, WARNING for problems worked around, ERROR for failures); `configure_logging()`, called by `main.py`, sends it to the console and `logs/monitor.log` through a filter that masks anything shaped like a token or key, every secret `config.py` read (the GitHub token, encrypted and decrypted, the encryption key, the Gemini key) and the home folder, which holds the user's name. Technicians are logged by id, never by name.
- `hardware_monitor/retry.py` — `retry()`: the one retry loop the project uses (a doubling wait, no wait after the last attempt, only the errors the caller lists as temporary are retried). Reading the data files and logs, log appends, the CSV report and resetting the demo logs retry a `PermissionError` 3 times, waiting 0.1 s and then 0.2 s.
- `hardware_monitor/api_client.py` — raw network communication, and only that: the one module that talks to the network (a test reads every module's imports to keep it so), and it imports nothing from the project but the retry loop.
  - `create_issue()` posts an alert to GitHub Issues and checks the reply. Every failure (no token, HTTP error, timeout, connection error, other request error, incomplete or invalid reply) comes back as a result instead of an exception. Temporary failures (timeouts, connection errors, a reply cut off mid-way, HTTP 429/5xx) are retried up to 3 attempts in total, waiting 1 s and then 2 s; permanent errors (401, 404, 422, …, a bad URL or header, a redirect loop) and incomplete replies are not retried. Error messages never include the token.
  - `triage_fault()` asks Gemini to triage a fault and returns a checked `Triage` (priority, cause, repair minutes, steps). Every failure (no key, HTTP error, timeout, a blocked or unfinished answer, an invalid triage) comes back as a result; timeouts, connection errors and HTTP 429/5xx are retried once; the key is sent in a header and never appears in an error. Each step is capped at 300 characters.
  - `find_open_issues()` GETs the open issues with the alert's labels (pull requests left out), so the same fault isn't posted twice. It has the retry the GET requirement asks for: a `Timeout` or `ConnectionError` (or HTTP 429/5xx) is retried, up to 3 attempts in total, waiting 1 s and then 2 s; permanent errors are not retried, and every entry is checked (number, link to that issue, title).
  - All three check the status with `raise_for_status()`, then the JSON, then every field they rely on.
  - Every failed result names its `ErrorCategory` in `.category`: `COMMUNICATION` (no reply: a timeout, connection error or other request error), `HTTP` (an error or unexpected status), `INVALID_JSON` (the reply, or Gemini's answer in it, isn't JSON) or `VALIDATION` (JSON with the wrong structure or values). Successes, and calls with no token or key (nothing sent), have none.
- `hardware_monitor/services.py` — the business logic and decisions made with the API data; it never touches the network itself. `report_fault()` posts nothing while this computer's issue for the same fault is still open (and posts anyway if that can't be checked), decides whether to ask Gemini, applies the triage, and composes the GitHub issue's text and labels; `apply_triage()` gives a call the more urgent of its own priority and Gemini's; `triage_summary()` computes the report's figures from the triages.
- `hardware_monitor/synthetic_sources.py` — synthetic mic/camera signals used by the dashboard.
- `hardware_monitor/config.py` — constants, GitHub token decryption and the Gemini key.
- `data/sample_data.jsonl` — 16 synthetic component records. `data/technicians.jsonl` — 10 synthetic technicians (an id and a name each); any of them can take any call.
- `tests/` — the pytest regression suite (see Testing). `.github/workflows/tests.yml` — runs it on GitHub.
- `AI_USAGE.md` — how AI was used. `pyproject.toml`, `requirements.txt`, `.gitignore` — project settings.

## Object-Oriented Programming (OOP)
*   **Composition (הרכבה):** `Workstation` contains a list of `HardwareComponent` objects and offers `add_component`, `remove_component`, `find_component` and `run_all_diagnostics`.
*   **Inheritance (הורשה):** `HardwareComponent` is the base class. `Camera` and `Microphone` are kinds of component and add their own fields (resolution, sample rate).
*   **Polymorphism (פולימורפיזם):** `run_diagnostics()` and `alert_priority()` are defined in the base class and overridden in each subclass. The diagnostics sweep calls them through the common type, with no `if/elif` on the object's class. A camera fault outranks a microphone fault, and a lost device outranks a misconfigured one.
*   **Validation:** `connection_status` and `MaintenanceRequest.status` are properties that raise `ValueError` with an explanation for an illegal value. `Workstation.add_component` refuses a component of another workstation or a reused id. The `from_dict` factories reject missing fields and wrong types.
*   **Other tools:** `@classmethod` factories (`from_dict`), a `@staticmethod` (`Camera.parse_resolution`), a computed property (`age_years`) and `__len__` on `AlertHistory`.

## Design patterns
| Pattern | Where | What it gives us |
|---|---|---|
| **Factory** | `HardwareComponent.from_dict` (also `Workstation.from_dict`, `Technician.from_dict`) | The loader passes in a JSONL record and gets back the right subclass (`Camera`, `Microphone`, …) without any `if/elif` on the type. The factory finds the subclasses through `__subclasses__()`, so adding a new kind of component needs no change to the factory. Each subclass validates its own fields. |
| **Strategy** | `AssignmentStrategy` in `maintenance.py` | Every technician can take any call, so the `MaintenanceDesk` hands all of them to a strategy object, with how many calls each has saved in the log. `FewestCallsStrategy` (default: a random draw between everyone, someone k calls ahead of the least-loaded weighing 1 / (1 + k)), `LeastBusyStrategy` (fewest open calls, then whoever waited longest) and `RoundRobinStrategy` (strict turns, whatever the load) are interchangeable: `MaintenanceDesk(..., assignment_strategy=RoundRobinStrategy())`, or swap `desk.assignment_strategy` on a running desk. |

## מבני נתונים (Data Structures)
| Need | Structure chosen | Why it fits |
|---|---|---|
| Full alert history in the order alerts happened | `list` (`AlertManager.alert_history`) | Ordered, changeable, duplicates allowed. |
| Heap entry for one alert | `tuple` `(priority, arrival_no, alert)` | A short fixed record. The arrival number breaks ties, so two `Alert` objects are never compared. |
| Alerts the help desk has acknowledged | `set` (`acknowledged_alert_ids`) | Unique IDs and fast `in` checks. `discard` is used because undoing a missing acknowledgement should not raise (unlike `remove`). |
| Rooms with both camera and microphone down | `set` intersection (`&`) | Workstations in both the camera-fault and microphone-fault sets. |
| Alerts still waiting for acknowledgement | `set` difference (`-`) | All alert IDs minus the acknowledged ones. |
| Find a technician by ID | `dict` `tech_id -> Technician` | Lookup by key. A duplicate ID in the file raises `ValueError` instead of overwriting. |
| Find a maintenance call by ID | `dict` `request_id -> MaintenanceRequest` (`MaintenanceDesk.requests`) | Lookup by key. |
| Alerts per workstation, calls per technician | `dict` grouping / counting | `group_by_workstation()` and the per-technician totals in the history report (`dict.get(key, 0)` because a new technician has no count yet). |
| Help-desk intake | `deque` FIFO (`intake_queue`) | Alerts are acknowledged in arrival order with `append`/`popleft`, so the oldest alert is never starved by newer ones and every user gets a fair, predictable answer. An empty queue returns `None` instead of crashing. |
| Technician dispatch | `heapq` (`_dispatch_heap`) | Urgency beats arrival time: **a lower number means a more urgent fault** (1 lost camera, 2 misconfigured camera, 3 lost microphone, 4 misconfigured microphone). Only `heappop` is used to read it, because the internal list is not fully sorted. |

**Comprehensions, unpacking and sorting** (all in `processing.py`): a list comprehension (`unresolved_alerts`), a set comprehension (`unique_error_types`), a dict comprehension (`group_by_workstation`), star unpacking (`most_urgent, *the_rest` in `most_urgent_and_rest`), `sorted` with a lambda returning a two-field tuple (`sorted_by_priority`), and `sorted` with the named function `workstation_then_priority` as the key (`sorted_by_workstation`).

### Operations that change a collection vs. ones that create a new one
| Changes the existing collection | Creates a new collection |
|---|---|
| `alert_history.append(alert)` | `sorted(alert_history, key=...)` |
| `intake_queue.append()` / `popleft()` | `[a for a in alert_history if not a.resolved]` |
| `heapq.heappush()` / `heappop()` on the dispatch heap | `{a.error_type for a in alert_history}` |
| `acknowledged_alert_ids.add()` / `discard()` | `{ws_id: [...] for ws_id in ...}` (`group_by_workstation`) |
| `Workstation.add_component()` | `camera_down & microphone_down`, `all_ids - acknowledged` |
| `technicians[tech_id] = technician` | `Workstation.components` (returns a copy, so callers can't change the internal list) |

## Data Source and Loading
*   **AI source:** `data/sample_data.jsonl` and `data/technicians.jsonl` were generated with Gemini ([chat](https://share.gemini.google/EGqCdrGbD0GU)) and then checked and corrected by hand. Details, the prompt link and all checks are in `AI_USAGE.md`. All names and IDs are invented.
*   **Record structure:** one JSON object per line, for example:
    `{"workstation_id": "WS-101", "workstation_name": "Front Desk PC", "location": "Main Lobby", "component_type": "camera", "component_id": "CAM-101", "name": "Lobby Cam", "connection_status": "ACTIVE", "camera_index": 0, "resolution": "1920x1080", "installed_on": "2021-02-15"}`
    Microphones have `sample_rate` and `sensitivity_db` instead of `camera_index` and `resolution`.
*   **Loading:** `repository.py` opens the file with `with open(..., encoding="utf-8")` and reads it one line at a time with `for line in file` (never `.read()` or `.readlines()`). Each line becomes a `dict` through `json.loads`, then an object through `Workstation.from_dict` and `HardwareComponent.from_dict` (which builds a `Camera` or `Microphone` from `component_type`). Technicians are loaded the same way through `Technician.from_dict`.
*   **Validation:** invalid JSON, missing fields, ids that are not text, an unknown component type, an illegal status or date, wrong field types, a duplicate `component_id`, contradicting workstation details and a duplicate `tech_id` all raise `ValueError` with the file name and line number. Deliberately faulty values such as a `0x0` resolution or a 4000 Hz sample rate are accepted, because they are test cases for `run_diagnostics()` to catch.
*   **Output files** (all in `logs/`, not committed):

    | File | Written by | Contents |
    |---|---|---|
    | `hardware_history.jsonl` | dashboard | every alert, one per line |
    | `maintenance_requests.jsonl` | dashboard | every change to a maintenance call (append-only; the last line per `request_id` is its current state) |
    | `monitor.log` | every command | what the program did: actions, events and failures, one line each with time, level and module; secrets masked |
    | `maintenance_report.csv` | all three commands | maintenance history report for Excel, built from `maintenance_requests.jsonl` |
    | `demo_hardware_history.jsonl`, `demo_maintenance_requests.jsonl` | demo | the demo's own alerts and calls, reset every run so they never mix with the real history |

## Iterators, Generators & Lazy Pipeline
*   **Iterable and Iterator:** `AlertHistory` stores the alerts and implements `__iter__`, which returns a **new** `ActiveAlertIterator` every time. The iterator keeps its own position, implements `__iter__` and `__next__`, skips resolved alerts (its special rule, stated in its name) and raises `StopIteration` at the end. The demo shows two administrators walking the same history independently.
*   **Generator:** `iter_disconnected_components()` uses `yield` to return disconnected components one at a time. The demo creates it (nothing is checked yet), takes one item with `next()`, continues with a `for` loop from where `next()` stopped, then shows that a second pass yields nothing and that a new generator starts over.
*   **Lazy pipeline:** `lazy_disconnected_pipeline()` chains three generator expressions: disconnected components → older than 3 years → component ID. The demo takes only the first **two** results with `itertools.islice`.
    *   *What starts it:* creating the pipeline does no work. Work begins only when the first result is requested (`islice` calls `next`), and each step then pulls one item at a time from the step before it.
    *   *What was never processed:* after the second ID the pipeline stops. With the sample data it inspects 5 of the 8 workstations; WS-106 to WS-108 and their components are never read.
    *   *List comprehension vs. generator expression:* list comprehensions would build three full intermediate lists, checking all 16 components at every step before anything is returned. Generator expressions build no lists and process only what is needed for the two results.
    *   *Why a new generator for another pass:* a generator only moves forward and is exhausted once it ends. To go through the data again from the start, `lazy_disconnected_pipeline()` must be called again.

## Context Manager
*   **`DiagnosticConnection`** (`context_managers.py`) guards a hardware test. `__enter__` claims a free diagnostic port, saves the component's current status and sets it to `TESTING`, then returns the component (`with DiagnosticConnection(c) as tested:`). `__exit__` always releases the port and sets a real status.
*   **When an exception happens inside the block:** `__exit__` still runs. It releases the port, sets the component to `ERROR` and returns `False`, so the exception is **not** hidden and the caller still sees it. The demo shows a normal run and a run that raises.

## Maintenance Subsystem (team of four extension)
*   **Business need:** an alert alone doesn't fix anything. Someone has to own each fault, and work must be spread fairly so urgent rooms are not stuck behind one overloaded technician.
*   **Entities:** `Technician` and `MaintenanceRequest` (`models.py`). Technicians are loaded from `data/technicians.jsonl`; maintenance calls are saved to `logs/maintenance_requests.jsonl`.
*   **Links between the files:** the maintenance log links each call to a technician (`assigned_tech_id`, from `technicians.jsonl`) and to a workstation and component (`workstation_id`, `component_id`). Technicians are not tied to workstations, since any of them can take any call, so a technician record is just an id and a name: `{"tech_id": "T1", "name": "Dana Levi"}`.
*   **Process:** every alert opens a maintenance call (`OPEN`), which is assigned automatically (`IN_PROGRESS`). Every technician can take any call, on any workstation. Each call is a random draw between every technician, weighted towards those with fewer calls saved in the log (resolved ones and earlier runs included): someone k calls ahead of the least-loaded technician weighs 1 / (1 + k), so anyone can still get the call. When the fault clears, the call is `RESOLVED`. Pausing the dashboard for a break does not resolve it.
*   **Link to the main system:** each call stores the `alert_id`, `workstation_id` and `component_id` of the alert that opened it.
*   **Data checks:** `find_unknown_technician_references` flags calls in the maintenance log assigned to a technician who does not exist in `technicians.jsonl`. The demo runs it on the real log and on a deliberately broken record.
*   **Reports (`reports.py`):** a workload report of open calls per technician, and a history report of every call with the technician who handled it, also exported to `logs/maintenance_report.csv` (opens in Excel).
