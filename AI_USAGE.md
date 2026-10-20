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
