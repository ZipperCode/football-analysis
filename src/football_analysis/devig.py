"""De-vigging methods to convert bookmaker odds into fair (no-vig) probabilities.

A bookmaker's offered odds embed a profit margin (the "vig" or overround), so the
raw implied probabilities ``1/odds`` sum to more than 1.0. De-vigging removes that
margin to recover the market's fair probability estimate for each outcome.

Three methods are provided:

- ``multiplicative``: spread the vig proportionally to each implied probability.
- ``power``: raise implied probabilities to a constant power ``k`` so they sum to 1;
  keeps every value in ``(0, 1)`` and corrects favourite-longshot bias. Default.
- ``shin``: Shin's insider-trading model. For two-outcome markets it reduces to the
  additive method; for three or more outcomes the insider proportion ``z`` is solved
  iteratively.

All functions are pure and return new dictionaries; inputs are never mutated.
"""

from __future__ import annotations

from enum import Enum

_TOLERANCE = 1e-9
_MAX_ITERATIONS = 100


class DevigMethod(str, Enum):
    """Supported de-vigging methods."""

    power = "power"
    multiplicative = "multiplicative"
    shin = "shin"


def _valid_odds(odds: dict[str, float]) -> dict[str, float]:
    """Return only the entries with odds strictly greater than 1.0."""
    return {selection: price for selection, price in odds.items() if price > 1.0}


def implied_probabilities(odds: dict[str, float]) -> dict[str, float]:
    """Return raw implied probabilities ``1/odds`` per selection (no normalization).

    Selections with odds <= 1.0 are skipped.
    """
    return {selection: 1.0 / price for selection, price in _valid_odds(odds).items()}


def overround(odds: dict[str, float]) -> float:
    """Return the booking percentage: the sum of raw implied probabilities.

    A fair two-way market sums to 1.0; real vigged markets sum to >1.0.
    """
    return sum(implied_probabilities(odds).values())


def devig_multiplicative(odds: dict[str, float]) -> dict[str, float]:
    """Normalize implied probabilities so they sum to 1.0 (proportional vig removal)."""
    implied = implied_probabilities(odds)
    total = sum(implied.values())
    if total <= 0.0:
        return {}
    return {selection: prob / total for selection, prob in implied.items()}


def devig_power(odds: dict[str, float]) -> dict[str, float]:
    """Solve for power ``k`` such that ``sum((1/odds_i) ** k) == 1`` via bisection.

    The sum of ``p_i ** k`` is monotonically decreasing in ``k`` for ``p_i in (0, 1)``,
    so a unique root exists and bisection converges. Falls back to the multiplicative
    result if the implied probabilities are degenerate.
    """
    implied = implied_probabilities(odds)
    if len(implied) < 2:
        return devig_multiplicative(odds)

    probs = list(implied.values())

    def total_for(power: float) -> float:
        return sum(prob ** power for prob in probs)

    low, high = 0.5, 1.5
    # Expand the bracket outward until it straddles the root (handles extreme vig).
    while total_for(high) > 1.0 and high < 100.0:
        high *= 2.0
    while total_for(low) < 1.0 and low > 1e-6:
        low /= 2.0

    k = 1.0
    for _ in range(_MAX_ITERATIONS):
        k = (low + high) / 2.0
        total = total_for(k)
        if abs(total - 1.0) < _TOLERANCE:
            break
        if total > 1.0:
            low = k
        else:
            high = k

    raw = {selection: prob ** k for selection, prob in implied.items()}
    normalizer = sum(raw.values())
    if normalizer <= 0.0:
        return devig_multiplicative(odds)
    return {selection: value / normalizer for selection, value in raw.items()}


def devig_shin(odds: dict[str, float]) -> dict[str, float]:
    """Apply Shin's insider-trading model to recover fair probabilities.

    Shin assumes a proportion ``z`` of bets come from informed traders and derives the
    fair probability for each outcome accordingly. For two outcomes this is equivalent
    to the additive method; for more outcomes ``z`` is solved by fixed-point iteration.
    """
    implied = implied_probabilities(odds)
    if len(implied) < 2:
        return devig_multiplicative(odds)

    booking = sum(implied.values())
    if booking <= 0.0:
        return {}

    # Two-outcome markets: Shin reduces to the additive method. Subtracting the vig
    # share (booking - 1) / n from each implied probability leaves a sum of exactly 1,
    # so no renormalization is needed.
    if len(implied) == 2:
        vig_share = (booking - 1.0) / 2.0
        return {selection: prob - vig_share for selection, prob in implied.items()}

    pi = {selection: prob / booking for selection, prob in implied.items()}

    z = 0.0
    for _ in range(_MAX_ITERATIONS):
        total_sqrt = sum(
            (z ** 2 + 4.0 * (1.0 - z) * (value ** 2) / booking) ** 0.5 for value in pi.values()
        )
        new_z = max(0.0, min((total_sqrt - 2.0) / (len(pi) - 2.0), 0.5))
        if abs(new_z - z) < _TOLERANCE:
            z = new_z
            break
        z = new_z

    raw = {
        selection: (((z ** 2 + 4.0 * (1.0 - z) * (value ** 2) / booking) ** 0.5) - z) / (2.0 * (1.0 - z))
        for selection, value in pi.items()
    }
    normalizer = sum(raw.values())
    if normalizer <= 0.0:
        return devig_multiplicative(odds)
    return {selection: value / normalizer for selection, value in raw.items()}


_DISPATCH = {
    DevigMethod.power: devig_power,
    DevigMethod.multiplicative: devig_multiplicative,
    DevigMethod.shin: devig_shin,
}


def devig(odds: dict[str, float], method: DevigMethod = DevigMethod.power) -> dict[str, float] | None:
    """Return fair probabilities summing to 1.0 using ``method``.

    Returns ``None`` when fewer than two valid outcomes are present (an incomplete
    market cannot be de-vigged), which signals callers to fall back to another path.
    """
    if len(_valid_odds(odds)) < 2:
        return None
    result = _DISPATCH[method](odds)
    return result or None
