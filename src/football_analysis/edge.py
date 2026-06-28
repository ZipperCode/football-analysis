"""Sharp-anchor edge logic: derive fair probabilities from the sharpest bookmaker.

Sharp books (e.g. Pinnacle) run low margins and are accepted as the most efficient
price. This module groups odds snapshots by market, finds the sharpest available book
per group via a priority list, and de-vigs its full outcome set to recover a fair
probability per selection. Callers then compute expected-value edge for the best
*soft* price against that fair probability.
"""

from __future__ import annotations

from dataclasses import dataclass

from football_analysis.devig import DevigMethod, devig
from football_analysis.models import OddsSnapshot

# Synthetic bookmaker name produced by aggregation; never a real sharp anchor.
_SYNTHETIC_BOOKMAKER = "market average"


@dataclass(frozen=True)
class FairLine:
    """Fair (no-vig) probabilities for one market/line, derived from a sharp anchor."""

    market_type: str
    line: str | None
    fair_probability: dict[str, float]
    anchor_bookmaker: str
    anchor_source: str
    method: str


def group_snapshots(
    snapshots: list[OddsSnapshot],
) -> dict[tuple[str, str | None], list[OddsSnapshot]]:
    """Group snapshots by ``(market_type, line)``.

    Mirrors the keying of ``ingestion.aggregate_market_prices`` minus ``match_id``,
    since all snapshots passed here belong to a single match.
    """
    groups: dict[tuple[str, str | None], list[OddsSnapshot]] = {}
    for snapshot in snapshots:
        groups.setdefault((snapshot.market_type.value, snapshot.line), []).append(snapshot)
    return groups


def _matches_priority(bookmaker: str, needle: str) -> bool:
    return needle.strip().lower() in bookmaker.strip().lower()


def find_sharp_anchor(
    group: list[OddsSnapshot],
    sharp_priority: list[str],
    method: DevigMethod,
) -> FairLine | None:
    """Return the de-vigged fair line from the highest-priority sharp book in ``group``.

    Bookmaker names are matched case-insensitively as substrings, in priority order, so
    ``"Pinnacle"`` matches ``"Pinnacle Sports"``. The synthetic ``"Market average"``
    book is never selected. Returns ``None`` when no priority book is present or its
    market cannot be de-vigged.
    """
    for needle in sharp_priority:
        for snapshot in group:
            if snapshot.bookmaker.strip().lower() == _SYNTHETIC_BOOKMAKER:
                continue
            if not _matches_priority(snapshot.bookmaker, needle):
                continue
            fair = devig(snapshot.outcome_odds, method)
            if fair is None:
                continue
            return FairLine(
                market_type=snapshot.market_type.value,
                line=snapshot.line,
                fair_probability=fair,
                anchor_bookmaker=snapshot.bookmaker,
                anchor_source=snapshot.source,
                method=method.value,
            )
    return None


def best_soft_price(
    group: list[OddsSnapshot],
    selection: str,
    anchor_bookmaker: str,
) -> float | None:
    """Return the best (highest) price for ``selection`` across non-anchor books.

    Falls back to ``None`` when no soft book quotes the selection. Excludes the anchor
    itself and the synthetic market-average book.
    """
    prices = [
        snapshot.outcome_odds[selection]
        for snapshot in group
        if selection in snapshot.outcome_odds
        and snapshot.bookmaker != anchor_bookmaker
        and snapshot.bookmaker.strip().lower() != _SYNTHETIC_BOOKMAKER
        and snapshot.outcome_odds[selection] > 1.0
    ]
    return max(prices) if prices else None
