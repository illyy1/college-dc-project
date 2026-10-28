"""Asynchronous information gathering: workstation status snapshots fetched
without changing the models, and a monitoring overview gathered from three
independent sources, one after another or all at once."""

import asyncio
from collections import Counter

from .models import Workstation
from .repository import load_workstations_from_jsonl, load_technicians_from_jsonl, load_maintenance_history

# simulated round trip to the server holding each source, so the overlap is visible
SOURCE_LATENCY_SECONDS = 0.2


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
    # gather() returns a list; list() also tells type checkers so, as its stubs say tuple
    return list(await asyncio.gather(
        *(_fetch_workstation_status(workstation) for workstation in workstations)
    ))


async def _gather_fleet_status(workstations_path: str) -> dict:
    """Source 1: the fleet, with every workstation's status fetched concurrently."""
    await asyncio.sleep(SOURCE_LATENCY_SECONDS)
    # the loaders do blocking file reads, so they run on a worker thread
    workstations = await asyncio.to_thread(load_workstations_from_jsonl, workstations_path)
    snapshots = await fetch_statuses_concurrent(workstations)
    statuses = Counter(item["status"] for snapshot in snapshots for item in snapshot["component_statuses"])
    return {"workstations": len(snapshots), "components_by_status": dict(statuses)}


async def _gather_technician_roster(technicians_path: str) -> list:
    """Source 2: the technicians' names, as listed in the data file."""
    await asyncio.sleep(SOURCE_LATENCY_SECONDS)
    technicians = await asyncio.to_thread(load_technicians_from_jsonl, technicians_path)
    return [technician.name for technician in technicians.values()]


async def _gather_maintenance_summary(maintenance_log_path: str) -> dict:
    """Source 3: how many maintenance calls exist and which are still open."""
    await asyncio.sleep(SOURCE_LATENCY_SECONDS)
    history = await asyncio.to_thread(load_maintenance_history, maintenance_log_path)
    open_calls = [call.request_id for call in history if call.status != "RESOLVED"]
    return {"total_calls": len(history), "open_calls": open_calls}


async def gather_overview_sequential(workstations_path: str, technicians_path: str,
                                     maintenance_log_path: str) -> dict:
    """Collect the three sources one after another; each waits for the last."""
    return {
        "fleet": await _gather_fleet_status(workstations_path),
        "technicians": await _gather_technician_roster(technicians_path),
        "maintenance": await _gather_maintenance_summary(maintenance_log_path),
    }


async def gather_overview_concurrent(workstations_path: str, technicians_path: str,
                                     maintenance_log_path: str) -> dict:
    """Collect the three independent sources at once.

    create_task starts all three before any of them is awaited, so their waits
    overlap and the total time is about that of the slowest source. gather
    then collects the results in order and raises the first error, if any.
    """
    fleet_task = asyncio.create_task(_gather_fleet_status(workstations_path))
    roster_task = asyncio.create_task(_gather_technician_roster(technicians_path))
    maintenance_task = asyncio.create_task(_gather_maintenance_summary(maintenance_log_path))
    fleet, technicians, maintenance = await asyncio.gather(fleet_task, roster_task, maintenance_task)
    return {"fleet": fleet, "technicians": technicians, "maintenance": maintenance}
