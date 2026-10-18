"""Small, self-contained examples of concurrency used by the monitor.

The async example fetches workstation status snapshots without changing the
models. The threaded example demonstrates protecting a shared alert log.
"""

import asyncio
import threading

from .models import Workstation
from .processing import Alert


async def _fetch_workstation_status(workstation: Workstation) -> dict:
    """Simulate an I/O-bound status request for one workstation."""
    await asyncio.sleep(0.01)
    return {
        "workstation_id": workstation.workstation_id,
        "name": workstation.name,
        "component_statuses": [
            {"component_id": item.component_id, "status": item.connection_status}
            for item in workstation.components
        ],
    }


async def fetch_statuses_sequential(workstations: list[Workstation]) -> list[dict]:
    """Fetch each workstation in order, waiting for each request to finish."""
    return [await _fetch_workstation_status(workstation) for workstation in workstations]


async def fetch_statuses_concurrent(workstations: list[Workstation]) -> list[dict]:
    """Fetch independent workstations concurrently while preserving input order."""
    return await asyncio.gather(
        *(_fetch_workstation_status(workstation) for workstation in workstations)
    )


class LockedAlertLog:
    """An in-memory alert log whose batch writes are protected by one lock."""

    def __init__(self):
        self._alerts: list[Alert] = []
        self._lock = threading.Lock()

    def append_batch(self, alerts: list[Alert]) -> None:
        """Append a worker's complete batch as one indivisible operation."""
        with self._lock:
            self._alerts.extend(alerts)

    def snapshot(self) -> list[Alert]:
        """Return a consistent copy of the current log."""
        with self._lock:
            return list(self._alerts)


def record_alerts_from_threads(batches: list[list[Alert]]) -> list[Alert]:
    """Have one thread per batch append to a shared log, then return its contents.

    Each batch remains contiguous in the log because the lock covers the full
    extend operation. The order between batches depends on thread scheduling.
    """
    alert_log = LockedAlertLog()

    def worker(batch: list[Alert]) -> None:
        alert_log.append_batch(batch)

    threads = [threading.Thread(target=worker, args=(batch,)) for batch in batches]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return alert_log.snapshot()
