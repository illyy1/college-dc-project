# Room Hardware Monitor (IT Asset & Alert System)

## Prerequisites & Installation
Install the required dependencies using the following command in your PyCharm terminal:
`pip install sounddevice numpy opencv-python pyinstaller`

## הצעת הפרויקט (Project Proposal)
*   **Description:** An IT information system that manages computer workstations and tracks hardware components to process automated alerts for failures.
*   **Business Need & Value:** Centralizes hardware inventory and categorizes alerts by priority, drastically reducing downtime and troubleshooting time in meeting rooms.
*   **Users:** IT Administrator (manages inventory/alerts) and Automated System Monitor (generates real-time alerts).
*   **Process & Data Flow:** Component fails -> Alert generated and placed in priority queue -> IT Admin processes alert -> Status updated. Synthetic data is loaded from a `.jsonl` file.
*   **Primary Use Cases:** 1. Prioritizing critical camera disconnections over standard microphone silence. 2. Filtering "active" alerts by specific workstations using lazy evaluation.
*   **Future Expansion:** Adding capabilities to send alerts when a monitor disconnects, when the system is set to mute, or when the wrong microphone is set as the default communication device.

## Object-Oriented Programming (OOP)
*   **Composition (הרכבה):** The `Workstation` class contains and manages a list of `HardwareComponent` objects, providing methods to add/remove devices[cite: 1].
*   **Inheritance (הורשה):** `HardwareComponent` is the base class. `Camera` and `Microphone` inherit from it to add specific attributes[cite: 1].
*   **Polymorphism (פולימורפיזם):** A `run_diagnostics()` method is defined in the base class and overridden in the children, allowing the system to test all components without `if/elif` type checks[cite: 1].

## מבני נתונים (Data Structures)
*   **`list` & `tuple`:** Used to store the ordered history of alerts and sort via multiple fields[cite: 1].
*   **`dict`:** Groups components by `Workstation` for fast O(1) lookups[cite: 1].
*   **`set`:** Tracks unique error types, preventing duplicate alert categories[cite: 1].
*   **`deque` (Queue):** Processes routine maintenance requests in a strict First-In-First-Out (FIFO) order[cite: 1].
*   **`heapq` (Priority Queue):** Ensures critical alerts bypass standard alerts (e.g., lower number = higher priority)[cite: 1].
*   **Comprehensions:** Uses list, dict, and set comprehensions for clean data filtering and indexing[cite: 1].

## Iterators, Generators & Lazy Pipeline
*   **Iterables & Iterators:** A custom `AlertHistory` iterable manages the collection, while an `ActiveAlertIterator` maintains state to specifically yield only "Unresolved" alerts[cite: 1].
*   **Lazy Pipeline (Generator Expression):** A 3-step lazy pipeline filters disconnected components -> filters by age -> extracts the ID. It evaluates one item at a time (saving memory) and halts execution exactly when the required amount of records is consumed[cite: 1].

## Context Managers
*   **`HardwareDiagnosticSession`:** A custom context manager used during component testing. It locks the component status to `TESTING` upon entry and safely restores it to `ACTIVE` upon exit, ensuring the system doesn't freeze even if an exception occurs[cite: 1].

## Data Source, Processing & Logging
*   **Data File:** `data/sample_data.jsonl` contains 15+ synthetic JSON records.
*   **Processing:** Reads the file line-by-line using `with open(...)` (strictly avoiding `.read()` or `.readlines()`)[cite: 1]. Converts JSON strings to dictionaries via `json.loads`, validates the data, and instantiates objects using `@classmethod` factories[cite: 1].
*   **Local Logging:** Processed alerts and system events are safely appended to `logs/hardware_history.jsonl` using a context manager to ensure permanent historical records.