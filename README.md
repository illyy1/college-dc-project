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
- The packages in `requirements.txt` (`requests`, `python-dotenv`, `cryptography`). The demo imports `requests` too, so install them before the first run.
- Tkinter, only for the live dashboard and statistics window (included with the standard Python installer on Windows and macOS).

## Installation
```
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```
Or `pip install -e .`, which installs the project as an editable package from `pyproject.toml` (same dependencies; keep it and `requirements.txt` in step). To build a standalone executable, also `pip install pyinstaller`.

**Keys for the real services (optional).** Copy `.env.example` to `.env` (ignored by git, never committed) and fill it in: an encrypted GitHub token and its key (`GITHUB_TOKEN`, `ENCRYPTION_KEY`) and a Gemini key from Google AI Studio (`GEMINI_API_KEY`). To create the GitHub pair:
```
python -c "from cryptography.fernet import Fernet; k = Fernet.generate_key(); print('ENCRYPTION_KEY=' + k.decode()); print('GITHUB_TOKEN=' + Fernet(k).encrypt(b'<your token>').decode())"
```
Without `.env` everything still runs; the dashboard just posts nothing and asks Gemini nothing.

## Running the project
| Command | What it does |
|---|---|
| `python main.py` | The default, offline (no network, no keys, no window): the step-by-step demonstration (12 steps), then the maintenance history report, printed and exported to `logs/maintenance_report.csv`. `--no-dashboard` does the same. |
| `python main.py --dashboard` | The demonstration, then the live monitoring dashboard for this computer with the live statistics window next to it. Its mic/camera signals are synthetic (`synthetic_sources.py`); with `.env` it posts real GitHub issues and asks the real Gemini. When it closes, the statistics window closes too and the report is published. |
| `python main.py --no-stats` | The dashboard without the statistics window. |
| `python main.py --live` | Only the joint process (demo step 12) against the real GitHub and Gemini, labelled `[REAL]`. Posts a real issue. |
| `python main.py --report` | Only the maintenance history report and its CSV. |
| `python main.py --stats` | Only the live statistics window: it re-reads the maintenance log every 2 s (so it follows a dashboard running in another terminal) and shows a summary, two pie charts (calls per technician, per alert type), alerts by type, each technician's calls and the latest calls. |

## Project layout
The course template's `project_name/` package is named `hardware_monitor/` here.
```
main.py                  entry point only: reads the flags, calls the package; no business classes
hardware_monitor/
  __init__.py            package definition and public API
  models.py              HardwareComponent (abstract), Camera, Microphone, Workstation,
                         Technician, MaintenanceRequest: OOP, validation, from_dict factories
  repository.py          reads data/*.jsonl line by line into objects (a bad line raises
                         DataFileError, a ValueError); reads and appends the logs
  processing.py          Alert, AlertManager (list, dict, set, deque, heapq, comprehensions,
                         sorting) and the polymorphic diagnostics sweep
  iterators.py           AlertHistory / ActiveAlertIterator, a generator, the 3-step lazy pipeline
  context_managers.py    DiagnosticConnection
  demo.py                the 12-step demonstration main.py runs, one function per requirement
  maintenance.py         MaintenanceDesk: opens, assigns and saves maintenance calls  [subsystem]
  reports.py             workload and history reports, CSV export, cross-file checks  [subsystem]
  strategies.py          Strategy: who gets a call (depends on nothing else in the package)
  factories.py           Factory: builds the HardwareComponent subclass a record names
  api_client.py          Stage 2: the only module that talks to the network (GitHub, Gemini)
  services.py            Stage 2: the business decisions made with that data
  async_processing.py    Stage 2: three sources gathered one after another or at once (asyncio)
  threaded_processing.py Stage 2: FaultCalls, the Lock that gives one fault one call (threads)
  retry.py               the one retry loop (GitHub, Gemini, log and report writes)
  config.py              settings, the GitHub token from .env, the Gemini key
  logging_module.py      safe logging to the console and logs/monitor.log, secrets masked
  dashboard.py           the live Tk dashboard and its monitoring threads (--dashboard)
  synthetic_sources.py   the simulated microphone and camera signals the dashboard reads
  stats.py, stats_viewer.py   the numbers and the window behind --stats
data/
  sample_data.jsonl      16 synthetic component records (AI-generated, see AI_USAGE.md)
  technicians.jsonl      10 synthetic technicians                                   [subsystem]
tests/                   the pytest suite; what it covers: tests/README.md
logs/                    everything the program writes (not committed)
.github/workflows/tests.yml   runs the suite on every push and pull request
AI_USAGE.md, pyproject.toml, requirements.txt, .env.example, .gitignore
```

## Object-Oriented Programming (OOP)
*   **Composition (הרכבה):** `Workstation` contains a list of `HardwareComponent` objects and offers `add_component`, `remove_component`, `find_component` and `run_all_diagnostics`.
*   **Inheritance (הורשה):** `HardwareComponent` is the base class. `Camera` and `Microphone` are kinds of component and add their own fields (resolution, sample rate).
*   **Polymorphism (פולימורפיזם):** `run_diagnostics()` and `alert_priority()` are defined in the base class and overridden in each subclass. The diagnostics sweep calls them through the common type, with no `if/elif` on the object's class. A camera fault outranks a microphone fault, and a lost device outranks a misconfigured one.
*   **Validation:** `connection_status`, `MaintenanceRequest.status` and `MaintenanceRequest.priority` (a whole number from 1 to 5) are properties that raise `ValueError` with an explanation for an illegal value, and a resolved call can't be reopened. `Workstation.add_component` refuses a component of another workstation or a reused id. The `from_dict` factories reject missing fields and wrong types.
*   **Other tools:** `@classmethod` factories (`from_dict`), a `@staticmethod` (`Camera.parse_resolution`), a computed property (`age_years`) and `__len__` on `AlertHistory`.

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

## שלב 2

### What was added in Stage 2
*   **The extension:** every new fault is checked against GitHub (is this computer's issue for it still open?) and triaged by Gemini before a technician is sent. The triage changes the maintenance call itself: the call takes the more urgent of the hardware rule's priority and Gemini's, and keeps Gemini's likely cause and repair estimate.
*   **Business value:** no duplicate issues for a fault already being handled, and urgent faults reach the front of the technicians' queue even when the hardware rule underrates them.
*   **The subsystem (team of four):** the external data flows from the monitor into the maintenance subsystem (`MaintenanceDesk`), and from there into the history report.
*   **New report metric:** the estimated open backlog in minutes, the estimated minutes per technician, and the most common causes (`services.triage_summary()`, printed by `python main.py --report`). It shows management how much repair work is still waiting and who carries it.

### How to run it
*   **Python 3.10+**, `pip install -r requirements.txt` ([Installation](#installation)). Environment variables, all optional and only for the real services: `GITHUB_TOKEN` + `ENCRYPTION_KEY`, `GEMINI_API_KEY`, in `.env` (copy `.env.example`; no secrets in the repository).
*   **Without network or keys (the default):** `python main.py`. Step 12, **the joint process**, sends a fault to the maintenance subsystem through simulated GitHub and Gemini clients passed into `services.report_fault()`; every line it prints says `[SIMULATED]`.
*   **Against the real services:** `python main.py --live` runs the joint process once with the real clients, labelled `[REAL]`, and posts a real issue for a demo call (never on the real maintenance log; with a key missing it says which and sends nothing). `python main.py --dashboard` runs the live dashboard, which does the same for every new fault.
*   **Tests:** `pip install pytest` (or `pip install -e ".[test]"`), then `pytest` from the project root: offline, no key, no real retry waits.

### How the code is organised
The tree, with a line for every module, is in [Project layout](#project-layout): the Stage 2 modules are `api_client.py`, `services.py`, `strategies.py`, `factories.py`, `async_processing.py`, `threaded_processing.py`, `retry.py`, `config.py` and `logging_module.py`. **The subsystem** is `maintenance.py` (`MaintenanceDesk`), `Technician` and `MaintenanceRequest` in `models.py`, and `reports.py`.
*   **The four extra team-of-four tests** (all in `tests/test_business_application.py`):
    1. joint process succeeds: `test_the_dashboard_applies_the_triage_to_the_call`;
    2. external service fails: `test_when_gemini_fails_the_call_and_the_labels_are_unchanged`;
    3. broken link between the systems: `test_triaging_an_unknown_call_raises`;
    4. the new report metric: `test_the_triage_summary`.
*   **Division of work:**
    *   **Elinor** — the external API, architecture and design patterns: `api_client.py`, `services.py`, `retry.py`, `config.py` + `.env.example`, `logging_module.py`, `strategies.py` (Strategy), `factories.py` (Factory) and dependency injection.
    *   **Illy** — automated testing and TDD: the `tests/` suite (fixtures, `parametrize`, `pytest.raises`, mocks, offline isolation) and the RED/GREEN/REFACTOR cycles.
    *   **Rachel** — concurrency and synchronization: `async_processing.py` (async collection, sequential vs. concurrent) and `threaded_processing.py` (the thread locks), with their tests in `tests/test_async_processing.py` and `tests/test_threaded_processing.py`.
    *   **Dania** — subsystem integration: the Gemini triage driving maintenance calls, the extended report in `reports.py`, the four extra tests above and the end-to-end demo in `main.py`.

### Key design decisions
*   **External services:** the [GitHub REST API, Issues](https://docs.github.com/en/rest/issues/issues) (a GET with `params` and a timeout lists the open issues with the alert's labels, a POST opens one) and the [Gemini API](https://ai.google.dev/gemini-api/docs) (`gemini-3.1-flash-lite` returns a triage as checked JSON: priority, likely cause, repair minutes, up to five steps). Only the alert itself goes to Gemini, never the computer's or technician's name. Every reply goes through `raise_for_status()`, JSON parsing and a check of every field it relies on, and every failure comes back as a result with its category: communication, HTTP, invalid JSON or invalid structure. A timeout or connection error is retried (GitHub up to 3 attempts in total, waiting 1 s and then 2 s); bad data and auth errors are never retried. The triage sets the call's priority (the more urgent of the rule's and Gemini's), its cause and estimate, the issue's `priority-N` label and the report's figures.
*   **On failure:** if the open-issue lookup fails, the issue is posted anyway (a duplicate is better than a lost alert). If Gemini fails or answers badly, the call keeps its own priority and nothing about it changes, so a bad answer can never lower urgency. The reason is logged.
*   **Two patterns:** **Strategy** chooses which technician gets a call (`FewestCallsStrategy`, `LeastBusyStrategy`, `RoundRobinStrategy`); it serves the maintenance subsystem directly and lets the rule change without touching the desk. `FewestCallsStrategy` (the default) is a random draw weighted towards whoever has fewer calls: someone k calls ahead of the least-loaded technician weighs 1 / (1 + k). **Factory** (`factories.create_component`, injected into `repository.load_workstations_from_jsonl`) builds the right `HardwareComponent` subclass from a data record, so loaders need no `if/elif` on the type; it finds the subclasses through `__subclasses__()`, so a new kind of component needs no change to it, and another factory loads an older export ("webcam", "mic") with no change to the loader.
*   **Dependencies from outside:** `MaintenanceDesk` gets its technicians, files and strategy, `report_fault()` gets its GitHub and Gemini functions, the loader gets its factory, and the dashboard gets its sources, alert manager, keys, clock and reporter, all through parameters that default to the real ones. Each part has one job, a new implementation plugs in without edits, and the tests pass fakes instead of the network.
*   **Independent tasks (simulated):** the fleet status, the technician roster and the maintenance summary are collected with `asyncio.create_task` + `gather` (with `asyncio.sleep` as the simulated latency) and compared with a sequential version. Results are identical and in input order (demo step 11).
*   **Shared resource and the Lock:** a fault's open maintenance call. The mic and camera threads can both see the same fault at the same time. Without a lock, both read "no open call for this fault", then both open one: two calls and two technicians for one fault. `threaded_processing.FaultCalls` makes the check and the open one step under one Lock, so 8 threads reporting one fault open exactly one call (demo step 11; `test_eight_threads_reporting_one_fault_open_one_call`, and a control test without the lock that opens several). The same lock in `MaintenanceDesk` stops two calls from getting the same `MR-` number. No network call is made while a lock is held.

### Proof that it works
*   **Tests:** 903 tests pass (`pytest`), run on every push by GitHub Actions. What each test file covers, the fixtures and the offline isolation: [tests/README.md](tests/README.md).
*   **Real run against the services** (2026-10-28, live dashboard with `.env`; the computer's name is left out here): a simulated microphone disconnect opened maintenance call `MR-0047`, Gemini returned a valid triage, and the GitHub issue was posted with it:
    ```
    Component: mic_conn · Maintenance Call: MR-0047
    Triage (AI-generated by Gemini; check before acting)
    - Priority: 2 (1 = most urgent)
    - Likely cause: cable
    - Estimated repair: about 10 min
    1. Check physical connection of the microphone cable to the computer port
    2. Inspect cable for visible damage or frays
    3. Try connecting the microphone to a different USB or audio port
    4. Open sound settings to verify if the input device is detected
    5. Restart the classroom computer to reset audio driver initialization
    ```
    The hardware rule gives a lost microphone priority 3; Gemini said 2, so the call was raised to 2 (the more urgent of the two) and the issue labelled `priority-2`. The cause and the 10-minute estimate went into the call and the history report.
*   **TDD (Red → Green → Refactor):** every Stage 2 feature was built test-first on its own branch. For example:
    *   Duplicate check (GET + retry): [`fc3b566`](../../commit/fc3b566) RED → [`928f7b7`](../../commit/928f7b7) GREEN → [`ca7a640`](../../commit/ca7a640) REFACTOR.
    *   Gemini triage driving the call: [`0b5ea66`](../../commit/0b5ea66) → [`86e044d`](../../commit/86e044d) → [`5c0627c`](../../commit/5c0627c).
    *   Error categories: [`5c86a78`](../../commit/5c86a78) → [`199452c`](../../commit/199452c) → [`50939bc`](../../commit/50939bc).
    *   The demo's joint process (step 12 and `--live`, tested in `tests/test_joint_process_demo.py`): [`5887bd7`](../../commit/5887bd7) RED → [`817e918`](../../commit/817e918) GREEN → [`7920043`](../../commit/7920043) REFACTOR. Offline, a fake triage of priority 2 raises the call from 3 to 2 (checked on the saved call), Gemini failing leaves it at 3, every line says `[SIMULATED]`, and `--live` is parsed but never run in the tests.
    *   The offline default run (`--dashboard` opens the window): [`adc9341`](../../commit/adc9341) RED → [`49289b4`](../../commit/49289b4) GREEN; a call's priority (1–5) and no reopening: [`7474699`](../../commit/7474699) RED → [`d5c3de5`](../../commit/d5c3de5) GREEN.
