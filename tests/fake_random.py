"""A predictable stand-in for Python's random, for tests of the
FewestCallsStrategy. The strategy makes a weighted draw between every
technician (random.choices); this stand-in records each draw's weights and
always takes the likeliest technician (the first, among equals)."""


class LikeliestPick:
    def __init__(self):
        self.draws = []  # one {tech_id: weight} per draw, in order

    def choices(self, population, weights, k=1):
        self.draws.append({t.tech_id: w for t, w in zip(population, weights, strict=True)})
        return [population[weights.index(max(weights))]]

    @property
    def favoured(self):
        """The tech_ids with the highest weight in each draw."""
        return [[tech_id for tech_id, w in draw.items() if w == max(draw.values())] for draw in self.draws]
