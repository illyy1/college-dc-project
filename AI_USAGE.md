# AI Usage

## AI tools used in this project

| Tool | Used for | Link / record |
|---|---|---|
| **Gemini** | Generating the synthetic data: `data/sample_data.jsonl` (16 component records) and `data/technicians.jsonl` (4 technicians) | **[Gemini chat with the final data prompt and answer](https://share.gemini.google/EGqCdrGbD0GU)** |
| Claude (Sonnet 5 / Opus 5.5, via Claude Code) | Code changes, reviews against the brief, and later additions to the data files (`installed_on`, `workstation_ids`) | Every commit carries a `Co-Authored-By: Claude` trailer; see section 2 |

> **Gemini data chat:** https://share.gemini.google/EGqCdrGbD0GU

This file records where AI tools were used in the project, the prompts given,
how the output was validated, and what was corrected by hand.

> **Note on prompts:** prompts marked _verbatim_ are the exact text sent to
> the tool. Prompts marked _summary_ are reconstructed from the commit
> messages, because the original chat text was not saved.

---

## 1. Synthetic data generation

### Prompts

**`data/sample_data.jsonl` — initial records** (Gemini)

The exact prompt and Gemini's answer are in the
[Gemini chat](https://share.gemini.google/EGqCdrGbD0GU).

The prompt asked for: one JSON object per line; 8 workstations, each with one
camera and one microphone (16 records); fields `workstation_id`,
`workstation_name`, `location`, `component_type`, `component_id`, `name`,
`connection_status`, plus `camera_index`/`resolution` for cameras and
`sample_rate`/`sensitivity_db` for microphones; a mix of healthy and faulty
components; and no real people, companies, addresses or devices.

**`data/technicians.jsonl`** (Gemini)

Generated in the same [Gemini chat](https://share.gemini.google/EGqCdrGbD0GU)
(also in the [original Gemini conversation](https://gemini.google.com/app/0530e97b34430e22)).

The prompt asked for: 4 fictional technicians with `tech_id`, `name` and
`specialty` (`Camera`, `Microphone` or `General`), at least two sharing a
specialty so that workload balancing can be shown.

**`installed_on` dates** (Claude, _summary_, commit `c9beb7e`)
> Give every component an `installed_on` date (ISO `YYYY-MM-DD`) so the lazy
> pipeline can filter disconnected components by age. Spread the dates across
> 2018–2025 so some components are older than the age threshold and some are
> newer.

### Validations performed

Each item below is enforced in code, so a bad record fails at load time with
the file name and line number.

| Check | Where |
|---|---|
| Each line is valid JSON and a JSON object | `repository._parse_record` |
| Required fields present, non-empty and text (`workstation_id`, `component_type`, `component_id`; `tech_id`, `name`, `specialty`) | `models._require_fields`, used by every `from_dict` |
| `specialty` is `Camera`, `Microphone` or `General` | `Technician.specialty` setter |
| `component_type` is `camera` or `microphone` | `HardwareComponent.from_dict` |
| `connection_status` is one of `ACTIVE`, `DISCONNECTED`, `TESTING`, `ERROR`, `MAINTENANCE` | `HardwareComponent.connection_status` setter |
| `installed_on` is a valid ISO date | `HardwareComponent.__init__` (`date.fromisoformat`) |
| `camera_index` is an integer; `resolution` is a string; `sample_rate` / `sensitivity_db` are numbers | `Camera._validate_record`, `Microphone._validate_record` |
| `component_id` is unique across the file | `load_workstations_from_jsonl` |
| Lines for the same workstation agree on its name and location | `load_workstations_from_jsonl` |
| `tech_id` is unique | `load_technicians_from_jsonl` |
| `workstation_ids` is a list of ids | `Technician.from_dict` |
| Every workstation a technician covers exists in `sample_data.jsonl` | `find_unknown_workstation_references` |
| Every technician named in the maintenance log exists in `technicians.jsonl` | `find_unknown_technician_references` (commit `e2ac8ec`) |

Manual checks:
- Counted the records: 16 component records (above the 15 minimum) across 8
  workstations, and 4 technicians.
- Confirmed that every name, location and ID is generic or invented
  (e.g. "Front Desk PC", "Main Lobby", "Dana Levi"). There are no real people,
  emails, phone numbers, serial numbers or addresses.
- Confirmed that the dataset produces the expected diagnostic results:
  `python main.py --no-dashboard` logs 5 alerts (CAM-103, CAM-104, CAM-107,
  MIC-105, MIC-107).

### Manual corrections
- **Intentional faults kept, not "fixed":** `CAM-103` has resolution `0x0`
  and `MIC-105` has a sample rate of `4000` Hz (below the 8000 Hz minimum).
  These look like data errors but are deliberate test cases that
  `run_diagnostics()` has to catch, so validation checks only their *types*,
  not their values.
- **Technician specialties:** checked that `specialty` values exactly match
  the component class names (`Camera`, `Microphone`) used to route
  maintenance calls, plus one `General` technician for overflow.
- **`workstation_ids` added to the technicians later:** the original technician
  records were not linked to any workstation. Each technician now lists the
  workstations they cover, checked against `sample_data.jsonl` at run time.
- **`installed_on` added later** (commit `c9beb7e`): the original records had
  no install date. The dates were added afterwards and checked to be in
  `YYYY-MM-DD` format and not in the future.

---

## 2. AI-assisted code changes (Claude Code)

Every commit below carries a `Co-Authored-By: Claude …` trailer. All prompts
in this section are _summaries_. For each commit, the change was reviewed,
run with `python main.py --no-dashboard` (and the dashboard where relevant),
and then committed.

| Commit | Date | Model | Request (_summary_) | Result / corrections |
|---|---|---|---|---|
| `6d9aeaf` | 2026-09-22 | Sonnet 5 | Split the single-file `main.py` into a package with separate modules for models, processing, repository, iterators, context managers and the dashboard; create the synthetic JSONL data file. | `hardware_monitor/` package created; `data/sample_data.jsonl` added. The dashboard was switched from real `sounddevice`/`opencv` input to simulated signals so that it can be tested without hardware. |
| `7bb371a` | 2026-09-22 | Sonnet 5 | Expose the package's public API from `__init__.py`. | `__init__.py` re-exports the main classes and loaders. |
| `69d3f3c` | 2026-09-22 | Sonnet 5 | Demonstrate set `add`/`discard`/intersection in `AlertManager`. | Added alert acknowledgement and a method that finds error types common to the critical heap and the maintenance queue. |
| `94dd81d` | 2026-09-24 | Opus 5.5 | Add a maintenance desk that assigns technicians and saves reports. | `MaintenanceDesk`, `data/technicians.jsonl`, an append-only maintenance log, and a CSV report. Also fixed the package import in `reports.py` and the request-reassignment bookkeeping. |
| `add0e3f` | 2026-09-26 | Opus 5.5 | Update the diagnostic session following instructor feedback. | Logs when the channel opens and closes, marks the component `ERROR` and re-raises on failure instead of suppressing the exception. |
| `2ee8cc2` | 2026-09-26 | Opus 5.5 | Add `requirements.txt`. | Lists runtime dependencies; README install step updated. |
| `603842e` | 2026-09-26 | Opus 5.5 | Complete Member 1's OOP requirements. | `MAINTENANCE` status; `__str__` on `HardwareComponent` and `Alert`. |
| `19eb072` | 2026-09-26 | Opus 5.5 | Add `DiagnosticConnection` and `__str__` methods; fix maintenance call bugs. | Port-claiming context manager. Fixes: the demo writes to its own log; open calls are reloaded on restart; corrupt log lines are skipped; appends start on a fresh line. |
| `e2ac8ec` | 2026-09-26 | Opus 5.5 | Demonstrate every requirement in `main.py` and validate data across files. | `--no-dashboard` flag; technician loader rejects missing fields and duplicate IDs; check for unknown technicians in the maintenance log. |
| `c9beb7e` | 2026-09-26 | Opus 5.5 | Make the lazy pipeline filter by component age and stop exactly at the limit; show concurrent iterators. | `installed_on` added to the data. Corrected an off-by-one: `enumerate`+`break` pulled one extra item, so it was replaced with `itertools.islice`. |
| `a6bad51` | 2026-09-26 | Opus 5.5 | Merge `main` into the feature branch. | Resolved the `context_managers.py` conflict by hand. Removed the duplicate `__str__` methods that the automatic merge had kept from both branches. |
| `a3a13c0` | 2026-09-26 | Opus 5.5 | Show `[DIAGNOSTICS]` messages only in demo step 3. | Added a `verbose` flag to `DiagnosticConnection`. |
| `135d1d2` | 2026-09-26 | Opus 5.5 | Move the demo out of `main.py`. | `demo.py` created; `main.py` reduced from 260 to 48 lines. |
| `1b8803a` | 2026-09-26 | Opus 5.5 | Simulate a busier room without flooding the screen or GitHub. | At most 2 popups on screen; at most one GitHub issue per fault type every 10 minutes; `SILENCE_DURATION` changed from 15 s to 8 s so silence alerts still fire. |
| `c666b54` | 2026-09-26 | Opus 5.5 | Guarantee at least one alert per minute. | Watchdog that forces a fault after 45 s of quiet. Checked with an 80-hour simulation: the longest gap between popups is 47 s (previously 109 s). |
| `bd7b8ec` | 2026-09-26 | Opus 5.5 | Spread maintenance calls across all technicians. | Ties go to the technician assigned least recently; the General technician takes overflow. |
| `e78a99e` | 2026-10-03 | Opus 5.5 | Check the project against the data requirements and fill the gaps. | Added `@classmethod` factories `Workstation.from_dict`, `HardwareComponent.from_dict` (dispatches to `Camera`/`Microphone`) and `Technician.from_dict`; `repository.py` now builds every object through them. Added validation for field types, duplicate component IDs and workstation consistency, with line numbers in errors. Tested against valid data and against 9 deliberately broken records. |
| `fd532cc`–`e102cb1` (PR #25) | 2026-10-03 | Claude Code | Check the project against the Stage 1 brief and fill the remaining gaps. | Each component subclass now ranks its own alerts (`alert_priority()`), replacing a `component_type == "Camera"` check. Every alert now enters both a FIFO help-desk queue and a technician dispatch heap. Added a named sort-key function, star unpacking, set difference, `Camera.parse_resolution` (`@staticmethod`), fuller generator/context-manager demos, `pyproject.toml`, and the missing README sections. Checked by running `python main.py --no-dashboard`. |
| `25b51d5`, `4d60957` | 2026-10-03 | Claude Code | Double-check the project against the Stage 1 brief and fix what is only partly met. | `config.py` imports `python-dotenv`/`cryptography` only when the dashboard needs the token, so the demo runs on plain Python. Technicians now list the workstations they cover, with a check against `sample_data.jsonl`. Checked by running `python main.py --no-dashboard` with both packages blocked, and by loading a technician with a malformed `workstation_ids`. |
| `1277ac1`, `9662d0f`, `d243017`, `a920565`, `7a3b1c8` | 2026-10-03/04 | Claude Code | Fill the remaining documentation and robustness gaps. | README documents `pyproject.toml`, the optional `.env` for GitHub alerts and every file in `logs/`; the demo uses every `Workstation` method; output is printed as UTF-8 so redirected Hebrew text doesn't crash on Windows; null required fields and a non-string `installed_on` are rejected; `--report` creates `logs/` if it is missing. Checked by running `python main.py --no-dashboard` and `--report`. |
| `a9b9331`–`21aaa7f` | 2026-10-04 | Claude Code | Final check of the whole project: requirements, bugs and consistency between classes and files. | Maintenance calls go only to technicians who cover the alert's workstation; the dashboard's microphone priority matches the model's scale; `main.py` no longer crashes in IDLE/Jupyter; ids must be text, `specialty` must be `Camera`/`Microphone`/`General`, and a workstation refuses duplicate or foreign components. Checked with `python main.py --no-dashboard`, the pytest suite, and deliberately broken records. |
| `8d06852` (PR #31) | 2026-10-20 | Claude Code | Check the thread locking in the project and fix the gaps. | `AlertManager.log_alert` is now locked, so the mic and camera threads can't interleave alert-log writes; the dashboard locks the check-then-open of maintenance calls, so one fault can't open two calls; popup state is read and changed under `_popup_lock`; `MaintenanceDesk.open_requests()` builds its list under the desk lock. Checked with 8 threads logging 1,600 alerts and opening/resolving 1,600 calls at once, and by running the dashboard. |
| `670923c` | 2026-10-20 | Claude Code | Implement asynchronous information gathering for at least three independent tasks using `create_task` or `gather`. | `gather_overview_concurrent` starts the fleet status, technician roster and maintenance history sources with `asyncio.create_task` and collects them with `asyncio.gather`; file reads run through `asyncio.to_thread`. Demo step 11 compares it with awaiting the sources one by one (0.22 s vs 0.68 s). Checked by running `python main.py --no-dashboard`, and with missing files to confirm a failing source raises cleanly. |
| `fd6c90e` | 2026-10-20 | Claude Code | Check that regression testing of Phase 1 behaviour is implemented, then add the missing tests. | The existing 30 tests left several Phase 1 features untested. `tests/test_phase1_regression.py` adds 33 tests: iterator, generator, lazy pipeline (including stopping at the limit), `DiagnosticConnection`, abstract `HardwareComponent` and `from_dict`, loader validation, reports/CSV, and a full demo run with its logs redirected. README gains a Testing section. All 63 tests pass; deliberately breaking the iterator, the context manager's `ERROR` status and the age filter each made tests fail. |
| `af35599` | 2026-10-20 | Claude Code | Add tests for business logic and edge cases: standard operations, invalid inputs and boundary conditions. | `tests/test_business_logic_edge_cases.py` (54 tests): thresholds, empty inputs, invalid records, maintenance rules, and the Phase 2 concurrency code, which had no tests before. Correction: the first versions of the overflow test and the lock tests passed even with the bug in place (the race was too rare to hit), so the overflow scenario was rebuilt and the lock tests now add a short pause at the critical moment. Breaking the 8000 Hz rule, the age cut-off, the overflow rule or either lock now makes tests fail; with the locks in place, the thread tests passed 5 runs out of 5. |
| `af35599` | 2026-10-20 | Claude Code | Implement design-pattern testing: the specific implementations and alternatives of the chosen patterns (e.g. Strategy, Factory). | Factory already existed (`from_dict`). Added a Strategy for technician assignment: `AssignmentStrategy`, with `LeastBusyStrategy` (the existing rule, kept as the default) and `RoundRobinStrategy`. `tests/test_design_patterns.py` (22 tests) covers both patterns and their alternatives; README gains a Design patterns section. All 139 tests pass, and swapping the default, ignoring the strategy, or hard-coding the factory each made tests fail. Correction: restoring a deliberately broken file with `git checkout` also wiped the uncommitted Strategy changes; they were re-applied and re-tested. |
| `1ed97b3` | 2026-10-20 | Claude Code | Add external API scenarios: simulating API success, HTTP errors, timeouts, and JSON responses missing required fields. | The GitHub call moved out of the dashboard into `github_client.create_issue()`, which now also checks the reply (`number`, `html_url`) and reports every failure as a result instead of raising. `tests/test_external_api.py` (35 tests) fakes `requests.post` for success, 401–503 errors, timeouts, network failures and incomplete replies. All 174 tests pass; the API tests pass with network access blocked, the suite passes without Tk, and accepting any 2xx, dropping the field check or dropping the timeout handling each made tests fail. |
| `12058d1` | 2026-10-20 | Claude Code | Add a retry mechanism: prove the system recovers after a temporary failure and stops/reports an error after the maximum attempts. | `create_issue()` retries timeouts, network errors and HTTP 429/5xx up to 3 attempts (waits of 1 s, then 2 s); permanent errors and incomplete 201 replies are not retried, since the issue may already exist. A retry after a timeout may occasionally duplicate an issue; this trade-off is documented in the code. 29 new tests (waits recorded, not slept) cover recovery, giving up, and no-retry cases. Allowing one attempt too many, retrying permanent errors, waiting after the last attempt, not doubling the wait, or not retrying timeouts each made tests fail. |
| `9fcc6b9` | 2026-10-20 | Claude Code | Add concurrency validations: the accuracy of the asynchronous collection and that the locks protect shared resources. | `tests/test_concurrency.py` (22 tests): async results match the data and keep input order; sources really overlap; blocking reads don't freeze the event loop; every locked method waits while its lock is held; shared state stays consistent under contention, with a control run that shows batches mixing up without the lock. Removing each of 4 locks and 4 ways of breaking the async code were all caught. Correction: removing the popup-slot lock was not caught at first (too fast for threads to collide), so lock-held checks were added for the dashboard's methods. 225 tests pass, also without Tk; 5 repeated runs passed every time. |
| `6e86727` | 2026-10-20 | Claude Code | Check that every testing requirement is implemented, then add tests for the gaps found. | All six testing requirements were met, but coverage showed three Phase 1 files barely tested: `dashboard.py` (33%), `synthetic_sources.py` (28%), `config.py` (59%). Added 54 tests: the dashboard logic with a fake Tk, a controllable clock and scripted signals (timing rules checked exactly at their limits); the simulated signals; token decryption with a throwaway key (the real `.env` is never read). Coverage rose to 97%, 100% and 100% (overall 82% → 97%). 16 deliberate breaks, one per rule, were all caught; 279 tests pass, also without Tk. |
| `ba11235` | 2026-10-21 | Claude Code | Mock or patch: isolate external dependencies so tests run without internet, an API key or real retry delays; mock the dependency (e.g. `requests`), never the function under test. | An audit found five gaps: retry waits patched through a test-only `_sleep` alias; the dashboard tests replacing the project's own `create_issue` and `get_github_token`; the concurrency tests replacing the real overview sources; no global guard; and hand-written fakes instead of `unittest.mock`. Fixed: `conftest.py` blocks non-local connections, hides the real `.env` and mocks the retry `sleep` for every test; `requests.post` is mocked with `autospec`; the real project functions now run. `tests/test_isolation.py` (8 tests) proves the guarantees. Correction: the first network block raised a `RuntimeError` that the code doesn't expect; it now raises the same error type as an offline machine. 289 tests pass with networking disabled and the `.env` moved aside. |
| `16a1151` | 2026-10-21 | Claude Code | Use `@pytest.mark.parametrize` for at least three test cases; then give the cases readable names. | Already met: 33 parametrized tests producing 123 cases. Added `ids=` to the 17 decorators whose 66 cases had auto-generated names (e.g. `[payload4]` → `[number-as-text]`). |
| `2d1954a` | 2026-10-21 | Claude Code | Use `pytest.raises` to verify exceptions for bad data and critical failures. | Already used 35 times. Tightened 13 checks that only checked the type, so they also check the message (Hebrew messages included, translated in comments) and that the failed action changed nothing. Added `tests/test_exceptions.py` (13 tests) for untested raise paths: a diagnostic that fails to start must release its port; a crashing strategy or unwritable log must leave no lock held; missing data files; clean `ValueError`s for bad JSON. Correction: Python 3.11 (CI) and 3.14 word some errors differently, so those checks accept both. 45 `pytest.raises` checks; not releasing the port and leaking the parser error were both caught. |
| `2d1954a` | 2026-10-21 | Claude Code | Check test coverage, then add tests for the four untested failure paths. | Coverage was 97.8%. Added `tests/test_failure_recovery.py` (11 tests), with each failure caused at the external dependency: a CSV locked by Excel, a crashing diagnostic, a log line half-written by a killed run, and `--report` end to end. Coverage 98.5% (97.7% with branches); breaking each recovery path was caught. |
| `2d1954a` | 2026-10-21 | Claude Code | Use fixtures to set up reusable mock data (e.g. a fake Workstation or Alert). | Added shared fixtures to `conftest.py` (`alerts` factory, `bench_workstation`, `alert_manager`, `make_desk`/`desk`) and replaced helpers duplicated across six files. Correction: the scripted refactor switched three tests that use their own technician team to the shared technicians; one would have kept passing without testing its scenario. Caught in review and fixed, along with two wrong parameters the script added. 314 tests pass; four earlier deliberate breaks were still caught. |
| `2d1954a` | 2026-10-21 | Claude Code | Use `tmp_path` when testing functions that read or write files. | Already met: every test that writes files uses `tmp_path`, and a before/after snapshot showed the suite changes no project file. Made that permanent: a session check in `conftest.py` fails the run if any test creates, changes or deletes a project file, which a deliberately misbehaving probe test confirmed. |
| `0d916da`, `2ed58e3`, `9b68c50` | 2026-10-25 | Claude Code | TDD: a live statistics view of the alert types and who took each call, with each technician's spread; then two pie charts (share of calls per technician and per alert type). | Asked first: a standalone viewer (`python main.py --stats`), all history, counts and share. Committed the failing tests first (two `RED:` commits: statistics, then the pie charts added before implementation), then `hardware_monitor/stats.py` and `stats_viewer.py` (`GREEN:`). The spec caught one bug: the spread's counts didn't line up ("1 call" vs "3 calls"); fixed in the code, the test unchanged. Two tests were added after the code, for safeguards coverage showed. Checked in a real Tk window. |
| `00679fc`, `f4ca3df` | 2026-10-25 | Claude Code | Do the thread locks or concurrency need anything for the viewer? | No lock needed: the viewer only reads, on Tk's single thread, and the dashboard writes from another process. Tests for reading mid-write and for a writer thread passed at once; a broken log line was reported on every 2-second refresh (RED), fixed with a `reported` set so each is reported once (GREEN). |
| `0fcbd40` | 2026-10-25 | Claude Code | Refactor after green. | One fake Tk for the dashboard and viewer tests (`tests/fake_tk.py`), shared `make_call`/`history`, `compute_stats` split into one function per section. The same 396 tests before and after. |
| `9e2defd`, `7a32294` | 2026-10-25 | Claude Code | The statistics viewer didn't open with the program: open it with the dashboard. | `python main.py` (and VS Code's Run button) starts the viewer as a separate process next to the dashboard and closes it with the dashboard, even on a crash; `--no-stats` leaves it out. `subprocess.Popen` and the dashboard window mocked in the tests; checked with a real background process. |
| `d167ff7`, `b974784` | 2026-10-25 | Claude Code | Remove the specialists: every technician takes any call; random among those with the fewest calls saved. | The rule changed twice while being specified (first "join the rotation", then fewest calls with random ties); uncommitted edits for the first version were discarded. `FewestCallsStrategy` became the default, `specialty` was removed from the data, model and reports. Two RED tests were themselves wrong (one still expected the old grouping, one read the random draw a call too early) and were fixed in the GREEN commit. |
| `adc8e65`, `bfa7181` | 2026-10-25 | Claude Code | Remove the workstation ids from technicians.jsonl and correct the tests that need them. | Warned first that the lists were the only link between the two data files, used by a Stage 1 cross-file check. Removed the lists, `covers()` and that check; the check between the maintenance log and `technicians.jsonl` remains; an old file that still has a list loads and ignores it. |
| `3289d19`, `f4802f0`, `f1019a7` | 2026-10-25 | Claude Code | Remove tests made redundant; make the tests work for any number of technicians; hire technicians to about 10. | Two redundant tests removed and the two stand-ins for `random` merged. Tests no longer depend on who is in `technicians.jsonl`: a test team built in the tests for exact numbers, general checks for the real file, and the assignment rules run for teams of 1, 2, 4 and 10 and the real staff. Six technicians hired (10 in all) with no test changed. Checked with the real file holding 1, 3, 4, 5 and 10 technicians. |
| `caa54da`, `0e97756`, `de40e60` | 2026-10-25 | Claude Code | Check that the tests aren't overfitted; correct what the check finds; one shared object per purpose. | The check changed things the behaviour shouldn't depend on: 5 tests broke on edits to `sample_data.jsonl`, up to 5 on a changed setting, and several used private dashboard state. Fixed with a frozen test fleet, settings read from the config (defaults pinned once per module), and one shared stand-in each for `requests.post`, the dashboard, threads, slowdowns, tokens and JSONL files. Correction: a lock test no longer caught a removed lock after the switch to public methods; a new test catches it. A transient error during one experiment came from `technicians.jsonl` being saved in the editor; re-runs were clean. |
| `3c3a4dc`, `010c637` | 2026-10-25 | Claude Code | Cover the missing tests: pie colours, the legend, a call logged twice. | RED: colours repeated after 8 technicians and the legend was cut off after 9 (both now real with 10 staff); the call-counted-once test passed at once. GREEN: generated colours beyond the palette, a canvas that grows with the legend. One mistake in the new code (the height compared with the diameter, not the pie's bottom), caught by a test. Checked in a real Tk window with the 10 technicians. |
| `38d9cd1`, `39df8fa`, `fb43fb9` | 2026-10-25 | Claude Code | Pick each call's technician at random, biased towards fewer calls, but so anyone can still get it. | RED, then GREEN: `FewestCallsStrategy` now draws between every technician with `random.choices`, weight `1 / (1 + k)` for someone k calls ahead of the least-loaded. Tests check the weights, that everyone is in every draw, and (with a seeded `Random`, 6000 draws) that anyone can win and fewer calls win more often. Correction: the RED step missed one test that assumed two calls go to two people; under the random draw it failed about 1 run in 3. Found by repeated runs and fixed without touching the app code; 10 full runs then passed. |
| `b9e2e44`, `55a2766`, `7130695` | 2026-10-25 | Claude Code | Are today's features covered for bugs? Then TDD the fix, the desk included; refactor. | A bug check (coverage, pyflakes, probes with badly typed log values) found that one saved call with a wrongly typed field (an open time that is a number or not a date, an id or error type that is a number or a list) froze the statistics window and stopped the desk from starting. Proposed skipping such calls everywhere so the desk and the viewer agree. RED: 24 tests, seven cases shared in `conftest.BAD_FIELDS`. GREEN: the log reader skips the line with one warning; the desk numbers from every line so a skipped number isn't reused. REFACTOR: one `make_bad_call` stand-in. Checked in a real Tk window. Correction: a temporary code break used to confirm the tests catch the old numbering was mis-indented the first time; redone, and the test caught it. |
| `8eedbdf`, `3ec0855` | 2026-10-25 | Claude Code | Review the explicit-network-errors commits for bugs and redundant code; would the findings still apply with an API key? Then fix them. | The review found that every request error was retried, permanent ones too (a redirect loop, a bad URL or header), and that requests quotes the header or URL in its messages, so a key saved with a trailing newline was printed on every retry (checked against the real requests). RED: 16 cases, including the key never appearing in an error or the console. GREEN: errors name the exception only; of the other request errors only a reply cut off mid-way is retried; the decrypted token is stripped. A duplicate ConnectTimeout test and an HTTPError case requests.post never raises were removed. Correction: the network-isolation test read the old error text and failed; it now watches the DNS lookup instead. |
| `9cc07bb`, `c437eb8` | 2026-10-25 | Claude Code | Did the review only cover the GitHub client? Implement error handling and a retry mechanism project wide; also check whether the tests are overinflated. | A survey found that only the GitHub client retried or handled its errors: a log write failing in a monitor thread ended that monitoring for the session, the CSV refresh caught only PermissionError, and main.py ended with a traceback for a locked report or a bad data file. RED: one shared `retry()` (test_retry.py), retried log and report writes, a call kept when its log line can't be saved, monitor loops restarted, and main.py exit codes. GREEN: retry.py, used by the GitHub client, the log appends and the CSV export; `DataFileError` for bad data lines. Checked for real against a CSV held open exclusively. Test audit: the new tests first repeated existing loader and GitHub retry tests, so the existing tests were tightened to `DataFileError` and the GitHub loop tests moved to test_retry.py instead; duplicates found elsewhere in the suite were reported, not removed. Correction: the Git Bash heredoc dropped backslashes in scripted edits (one broke a string in main.py); caught by the tests and redone with the editor. |

### Not AI-generated
- `bb21a63` — `Technician` and `MaintenanceRequest` models and the reporting
  module, written by a team member.
- Commits before 2026-09-22 (initial monitoring app, GitHub alerting, token
  encryption).

---

## 3. Memory-efficiency rule (line-by-line reading)

All JSONL loaders in `hardware_monitor/repository.py` read with
`with open(...)` and `for line in file:`, and none of them use `.read()` or
`.readlines()`. The only `.read(1)` in the file is in `append_jsonl_record`.
When writing, it reads the last byte of the log to check whether the log ends
with a newline. It does not parse data.
