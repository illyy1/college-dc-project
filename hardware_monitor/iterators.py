"""Lazy evaluation: a custom Iterable/Iterator pair, a yield-based generator,
and a 3-step lazy generator-expression pipeline."""


class AlertHistory:
    """Custom Iterable wrapping an ordered collection of alerts."""

    def __init__(self, alerts):
        self._alerts = list(alerts)

    def __iter__(self):
        return ActiveAlertIterator(self._alerts)

    def __len__(self):
        return len(self._alerts)


class ActiveAlertIterator:
    """Custom Iterator that walks an alert list but only ever yields the
    unresolved ones, skipping resolved alerts transparently."""

    def __init__(self, alerts):
        self._alerts = alerts
        self._index = 0

    def __iter__(self):
        return self

    def __next__(self):
        while self._index < len(self._alerts):
            alert = self._alerts[self._index]
            self._index += 1
            if not alert.resolved:
                return alert
        raise StopIteration


def iter_disconnected_components(workstations):
    """Generator (yield): lazily walks every workstation's components and
    surfaces the ones currently DISCONNECTED, one at a time."""
    for workstation in workstations:
        for component in workstation.components:
            if component.connection_status == "DISCONNECTED":
                yield component


def lazy_disconnected_pipeline(workstations, min_age_years=3):
    """3-step lazy evaluation pipeline built from generator expressions:
    disconnected components -> older than min_age_years -> their ids.
    Nothing is computed until the caller iterates it, and each step pulls one
    item at a time, so taking the first N results with itertools.islice stops
    the whole pipeline right after the Nth id — the rest is never examined."""
    disconnected = (c for ws in workstations for c in ws.components
                    if c.connection_status == "DISCONNECTED")                                    # step 1: filter disconnected
    aging = (c for c in disconnected
             if c.age_years is not None and c.age_years >= min_age_years)                        # step 2: filter by age
    component_ids = (c.component_id for c in aging)                                              # step 3: extract id
    return component_ids
