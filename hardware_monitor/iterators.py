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


def lazy_disconnected_pipeline(workstations):
    """3-step lazy evaluation pipeline built from generator expressions.
    Nothing is computed until the caller iterates it, so consuming only the
    first few results (e.g. via itertools.islice) never touches the rest."""
    all_components = (component for ws in workstations for component in ws.components)          # step 1: flatten
    disconnected = (c for c in all_components if c.connection_status == "DISCONNECTED")          # step 2: filter
    component_ids = (c.component_id for c in disconnected)                                       # step 3: project
    return component_ids
