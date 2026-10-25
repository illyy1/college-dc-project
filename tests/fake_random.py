"""A predictable stand-in for Python's random, for tests of the
FewestCallsStrategy: it always takes the first option, and records which
technicians each draw was between."""


class FirstPick:
    def __init__(self):
        self.offered = []  # the tech_ids of each draw, in order

    def choice(self, options):
        self.offered.append([t.tech_id for t in options])
        return options[0]
