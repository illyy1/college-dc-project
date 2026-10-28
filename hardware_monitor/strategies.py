"""Strategy pattern: how the maintenance desk picks the technician for a call.

There are no specialists: every technician can take any call, of any kind,
on any workstation. The desk hands every technician to an
AssignmentStrategy object, so the rule can be swapped without touching the
desk. The default, FewestCallsStrategy, draws at random between everyone,
favouring those with fewer calls saved in the log; LeastBusyStrategy and
RoundRobinStrategy are alternatives."""

import random
from abc import ABC, abstractmethod
from datetime import datetime


def open_call_count(technician):
    return sum(1 for r in technician.assigned_requests if r.status != "RESOLVED")


class AssignmentStrategy(ABC):
    """Strategy interface: picks one technician from the candidates (every
    technician). last_assigned_at maps tech_id -> when they were last given a
    call; calls_taken maps tech_id -> how many calls they have been given in
    total, as saved in the log (technicians with none may be missing)."""

    @abstractmethod
    def choose(self, candidates, last_assigned_at, calls_taken):
        """Returns one technician from candidates."""


def _last_assigned(technician, last_assigned_at):
    return last_assigned_at.get(technician.tech_id, datetime.min)


class FewestCallsStrategy(AssignmentStrategy):
    """Default: a random draw between every technician, weighted towards
    fewer calls saved in the log (resolved ones and earlier runs included).
    Someone k calls ahead of the least-loaded technician weighs 1 / (1 + k),
    so anyone can still get the call, and the bias depends on the gap, not
    on the totals. rng: anything with a choices() method; Python's random
    by default."""

    def __init__(self, rng=random):
        self.rng = rng

    def choose(self, candidates, last_assigned_at, calls_taken):
        counts = [calls_taken.get(t.tech_id, 0) for t in candidates]
        fewest = min(counts)
        weights = [1 / (1 + count - fewest) for count in counts]
        return self.rng.choices(candidates, weights, k=1)[0]


class LeastBusyStrategy(AssignmentStrategy):
    """Alternative: fewest open calls; ties go to whoever was assigned least
    recently, so equally busy technicians take turns."""

    def choose(self, candidates, last_assigned_at, calls_taken):
        return min(candidates, key=lambda t: (open_call_count(t), _last_assigned(t, last_assigned_at)))


class RoundRobinStrategy(AssignmentStrategy):
    """Alternative: strict turns. Whoever was assigned least recently gets
    the call, however many open calls they have."""

    def choose(self, candidates, last_assigned_at, calls_taken):
        return min(candidates, key=lambda t: _last_assigned(t, last_assigned_at))
