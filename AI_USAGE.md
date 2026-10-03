# AI Usage

This file records where AI tools were used in the project, the prompts given,
how the output was validated, and what was corrected by hand.

> **Note on prompts:** prompts marked _verbatim_ are the exact text sent to
> the tool. Prompts marked _summary_ are reconstructed from the commit
> messages, because the original chat text was not saved. Replace a summary
> with the exact prompt if you still have it.

---

## 1. Synthetic data generation

### Tools
- Gemini — [first draft of the workstation/component and technician records.](https://share.gemini.google/EGqCdrGbD0GU)
- Claude (Sonnet 5 / Opus 5.5, via Claude Code) — later extensions to the
  data files (commits `6d9aeaf`, `94dd81d`, `c9beb7e`).

### Prompts

**`data/sample_data.jsonl` — initial records**

The exact prompt and Gemini's answer are in the
[shared Gemini chat](https://share.gemini.google/EGqCdrGbD0GU) linked above.

The prompt asked for: one JSON object per line; 8 workstations, each with one
camera and one microphone (16 records); fields `workstation_id`,
`workstation_name`, `location`, `component_type`, `component_id`, `name`,
`connection_status`, plus `camera_index`/`resolution` for cameras and
`sample_rate`/`sensitivity_db` for microphones; a mix of healthy and faulty
components; and no real people, companies, addresses or devices.

**`data/technicians.jsonl`**

Generated in the same [Gemini chat](https://share.gemini.google/EGqCdrGbD0GU).

The prompt asked for: 4 fictional technicians with `tech_id`, `name` and
`specialty` (`Camera`, `Microphone` or `General`), at least two sharing a
specialty so that workload balancing can be shown.

**`installed_on` dates** (_summary_, commit `c9beb7e`)
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
| Required fields present and non-empty (`workstation_id`, `component_type`, `component_id`; `tech_id`, `name`, `specialty`) | `models._require_fields`, used by every `from_dict` |
| `component_type` is `camera` or `microphone` | `HardwareComponent.from_dict` |
| `connection_status` is one of `ACTIVE`, `DISCONNECTED`, `TESTING`, `ERROR`, `MAINTENANCE` | `HardwareComponent.connection_status` setter |
| `installed_on` is a valid ISO date | `HardwareComponent.__init__` (`date.fromisoformat`) |
| `camera_index` is an integer; `resolution` is a string; `sample_rate` / `sensitivity_db` are numbers | `Camera._validate_record`, `Microphone._validate_record` |
| `component_id` is unique across the file | `load_workstations_from_jsonl` |
| Lines for the same workstation agree on its name and location | `load_workstations_from_jsonl` |
| `tech_id` is unique | `load_technicians_from_jsonl` |
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
| _(this PR)_ | 2026-10-03 | Claude Code | Check the project against the Stage 1 brief and fill the remaining gaps. | Each component subclass now ranks its own alerts (`alert_priority()`), replacing a `component_type == "Camera"` check. Every alert now enters both a FIFO help-desk queue and a technician dispatch heap. Added a named sort-key function, star unpacking, set difference, `Camera.parse_resolution` (`@staticmethod`), fuller generator/context-manager demos, `pyproject.toml`, and the missing README sections. Checked by running `python main.py --no-dashboard`. |

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
