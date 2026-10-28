# The test suite

What the 900+ tests in this folder cover and how they stay isolated. Run them from the project root with `pip install -r requirements.txt pytest`, then `pytest`. The README's "שלב 2 → Proof that it works" sums up the results.

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
- **Applied to every test** (`tests/conftest.py`): connecting to any non-local address fails as if the machine were offline, and every `*_proxy` variable is cleared, so a proxy on localhost can't pass a request on. `load_dotenv` and the token variables are cleared, so the real `.env` is never read. The `sleep` between retries (GitHub, log and report writes) is a `unittest.mock` that records the waits instead of sleeping.
- **`requests.post`** is patched with `mock.patch(..., autospec=True)`, so a call the real function would reject fails the test. Its replies are `Mock(spec=requests.Response)`. `create_issue`, the retry loop and the dashboard's posting code all run for real against it.
- **Tk, the clock and the mic/camera signals** are replaced in the dashboard tests. The token is encrypted with a throwaway key and decrypted by the real `get_github_token`.
- **Wrappers around project code** (to slow a critical section or record when something runs) always call the real function.
- `tests/test_isolation.py` proves these guarantees:
  - a real request fails as if offline, and `requests` sees no proxy;
  - no token can be loaded;
  - exhausting 6 attempts (31 s of real waits) takes no real time;
  - the function under test is the real one;
  - the `requests` mock rejects a call the real `requests.post` would reject.

The suite guarantees that new code doesn't break the Phase 1 logic:
- `tests/test_models.py`, `tests/test_processing.py`, `tests/test_maintenance.py` — the models and validation (including a maintenance call's priority, 1 to 5, and a resolved call that can't be reopened), alert queues and data structures, and the maintenance desk.
- `tests/test_phase1_regression.py` — every other Phase 1 behaviour: the custom iterator, the generator, the lazy pipeline (including stopping at the limit), `DiagnosticConnection`, the abstract `HardwareComponent` and its `from_dict` factory, loader validation, the reports, and a run of the whole demo (all 11 steps).
- `tests/test_business_logic_edge_cases.py` — business logic and edge cases:
  - **Boundaries:** the 8000 Hz sample-rate threshold, the 3-year age cut-off, the smallest valid resolution, a limit of 0.
  - **Empty inputs:** empty files, fleets and alert queues.
  - **Invalid inputs:** wrong types, blank ids, impossible dates, records that aren't JSON objects, foreign or duplicate components.
  - **Maintenance rules:** a workstation in no technician's list still gets a technician, two technicians share open calls evenly, open calls survive a restart.
  - **Phase 2 concurrency:** threads never lose an alert record, batches stay together, and the async overview matches the sequential one.
- `tests/test_design_patterns.py` — the two design patterns (see the README's "Key design decisions"):
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
- `tests/test_joint_process_demo.py` — the demo's joint process (step 12), written test-first (TDD, the `feature/joint-process-demo` branch), offline: a fault's call goes from priority 3 to 2 with a simulated triage (checked on the saved call), the report's triage figures include it, a failing Gemini leaves the call at 3 while the issue is still posted, every line says `[SIMULATED]`, the real `report_fault()` gets the simulated clients, nothing reaches the real services, and `--live` is parsed but never run.
- `tests/test_factory_injection.py` — the Factory injected into the workstation loader, written test-first (TDD, the `feature/factory-injection` branch): the factory passed in builds every component, a factory for an older export loads it with no change to the loader, an injected factory's errors still name the file and line, and the default is `factory.create_component`.
- `tests/test_pattern_modules.py` — each design pattern in its own module, written test-first (TDD, the `feature/pattern-modules` branch): `assignment.py` holds only the strategies and imports nothing from the package, `factory.py` imports only the models and defines no classes, `HardwareComponent.from_dict` hands records to the factory, and the old import paths still work.
- `tests/test_dependency_injection.py` — dependency injection, written test-first (TDD, the `feature/dependency-injection` branch): each dependency passed to the dashboard is kept and is the one used (sources, alert manager, clock, thread starter, reporter, keys without reading `.env`), a new kind of camera works unchanged, the defaults are the real implementations, and `report_fault()` works with other GitHub/Gemini clients. The shared `dash` fixture passes its fakes through the constructor too; only Tk is patched.
- `tests/test_logging_module.py` — safe logging, written test-first (TDD, the `feature/safe-logging` branch): the masking of secrets, registered values and the home folder (in messages, arguments and tracebacks, in the file and on the console); the log file's format; loggers instead of `print()` in the operational modules; each event at its level; no technician's or computer's name in any record. The other tests read the log with the `logged` fixture.
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
  - **`--stats`, and the default run:** plain `python main.py` runs the offline demo and report; only `--dashboard` (or `--no-stats`) opens the dashboard and the viewer.
- `tests/test_failure_recovery.py` — real-world failures the system must survive. Each failure is caused at the external dependency, and the code that handles it runs for real:
  - **The CSV report locked by Excel:** the call is still opened or resolved and saved, a warning is printed, and the CSV catches up on the next change.
  - **A diagnostic that crashes:** the component is marked `ERROR` and alerted, and the sweep goes on.
  - **A log line half-written when the program was killed:** the next record starts on a fresh line, and the desk keeps numbering correctly.
  - **`--report` end to end:** it prints the history and exports the CSV, creating the folder if needed. Unlike the automatic refresh, it fails loudly if the CSV can't be written.
  - **A file held for a moment by another program** (antivirus, OneDrive, the search indexer): reading a data file or log, a log or report write, and resetting a demo log are retried; a full disk is not.
  - **A maintenance log that can't be written:** the call is still opened and kept in the run, and the log catches up on the call's next change. Any CSV write error, not only Excel's lock, leaves the call alone.
  - **The live dashboard keeps monitoring:** a fault still gets its call when the alert log can't be written, and a monitoring loop that crashes is reported and restarted.
  - **`main.py` explains instead of a traceback:** a report it can't write, or a data file or log that is missing, broken, unreadable or still locked, gives a one-line message and exit code 1, and so does `--dashboard` on a Python without tkinter. A bug is still raised, and a dashboard crash is not hidden by a failed report.
- `tests/test_retry.py` — the one retry mechanism the GitHub client, the log writes and the report share: recovery with a doubling wait, giving up with the last error, only the listed errors retried, and the attempt limit checked.
- `tests/test_concurrency.py` — concurrency validations. These tests are deterministic: they hold a lock and check that callers wait for it, or slow down a critical section so that code without the lock would fail every time.
  - **Async accuracy:** results match the data counted independently. Results stay in input order even when the tasks finish in reverse order, and repeated runs give identical results.
  - **Really concurrent:** all three sources start before any finishes, and all 8 workstation fetches are in flight at once (the sequential versions run one at a time). Blocking file reads don't freeze the event loop, and a failing source leaves no unhandled task errors.
  - **Locks taken:** every method of `MaintenanceDesk`, `AlertManager`, `LockedAlertLog` and the dashboard that touches shared state waits while its lock is held.
  - **Shared state stays consistent:** batches stay together, and a control run without the lock shows them getting mixed up. Snapshots never see half a batch, and concurrent calls and resolutions keep the numbering, workloads and log consistent. A fault reported by 8 threads opens one call, popup slots are never shared or overbooked, and only one GitHub issue opens per window. Mixed dashboard operations finish without deadlock.

GitHub Actions (`.github/workflows/tests.yml`) runs the suite on every pull request and every push to `main`, so a change that breaks old behaviour is caught before it is merged.
