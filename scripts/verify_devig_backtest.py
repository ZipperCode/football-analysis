"""Old-vs-new edge comparison: the gate for flipping ``devig.enabled: true``.

Runs the same synthetic fixtures through ``score_match`` twice — once with sharp-anchor
de-vigging off (legacy market-average edge) and once on (EV-form edge against a Pinnacle
anchor) — and reports how the edge distribution and recommendation counts shift.

This is an analysis script: it prints a comparison table and asserts only the invariants
that must hold for the rollout to be safe (disabled path unchanged; enabled path produces
EV-form edge with fair probabilities). Use the printed deltas to decide whether the
value_score/min_edge coefficients need recalibration before enabling in production.
"""

from __future__ import annotations

from datetime import datetime, timezone

from football_analysis.models import Match, OddsSnapshot, RecommendationStatus
from football_analysis.scoring import score_match
from football_analysis.settings import load_settings


def _match(match_id: str) -> Match:
    return Match(
        id=match_id,
        league="England - Premier League",
        home_team=f"{match_id} Home",
        away_team=f"{match_id} Away",
        kickoff_at=datetime(2026, 2, 1, 15, 0, tzinfo=timezone.utc),
        data_completeness=0.95,
    )


def _odds(match_id: str, books: dict[str, dict[str, float]]) -> list[OddsSnapshot]:
    """Build per-bookmaker 1x2 snapshots, then stamp market_average/best_price (as ingestion does)."""
    selections = sorted({sel for prices in books.values() for sel in prices})
    averages = {
        sel: round(sum(p[sel] for p in books.values() if sel in p)
                   / sum(1 for p in books.values() if sel in p), 4)
        for sel in selections
    }
    best = {sel: max(p[sel] for p in books.values() if sel in p) for sel in selections}
    now = datetime.now(timezone.utc)
    snapshots = []
    for i, (bookmaker, prices) in enumerate(books.items()):
        snapshots.append(
            OddsSnapshot(
                id=f"{match_id}-{i}",
                match_id=match_id,
                market_type="1x2",
                source="odds_api_io",
                bookmaker=bookmaker,
                outcome_odds=dict(prices),
                market_average=averages,
                best_price=best,
                collected_at=now,
            )
        )
    return snapshots


# Synthetic fixtures: a soft book offers a generous price vs. a sharp (Pinnacle) anchor.
FIXTURES = {
    "epl-soft-overpriced-home": {
        "Pinnacle": {"HOME": 2.00, "DRAW": 3.50, "AWAY": 4.00},
        "Bet365": {"HOME": 2.20, "DRAW": 3.40, "AWAY": 3.80},
        "William Hill": {"HOME": 2.15, "DRAW": 3.45, "AWAY": 3.90},
    },
    "epl-efficient": {
        "Pinnacle": {"HOME": 2.50, "DRAW": 3.30, "AWAY": 3.00},
        "Bet365": {"HOME": 2.48, "DRAW": 3.28, "AWAY": 2.98},
        "William Hill": {"HOME": 2.49, "DRAW": 3.29, "AWAY": 2.99},
    },
    "epl-soft-overpriced-away": {
        "Pinnacle": {"HOME": 3.80, "DRAW": 3.50, "AWAY": 2.05},
        "Bet365": {"HOME": 3.70, "DRAW": 3.40, "AWAY": 2.25},
        "William Hill": {"HOME": 3.75, "DRAW": 3.45, "AWAY": 2.18},
    },
}


def _run(enabled: bool) -> list[dict]:
    settings = load_settings()
    settings.devig = settings.devig.model_copy(update={"enabled": enabled})
    rows = []
    for match_id, books in FIXTURES.items():
        rec = score_match(_match(match_id), _odds(match_id, books), [], settings)
        ob = rec.odds_basis
        rows.append(
            {
                "match": match_id,
                "status": rec.status.value,
                "selection": rec.selection,
                "edge": ob.get("edge"),
                "edge_method": ob.get("edge_method"),
                "fair_prob": ob.get("fair_probability"),
                "value": rec.value_score,
            }
        )
    return rows


def main() -> None:
    off = _run(enabled=False)
    on = _run(enabled=True)

    print(f"{'match':<32} {'edge_off':>9} {'method_off':>16} | {'edge_on':>9} {'method_on':>20} {'fair_p':>8}")
    print("-" * 100)
    for ro, rn in zip(off, on):
        print(
            f"{ro['match']:<32} {ro['edge']:>9.4f} {ro['edge_method']:>16} | "
            f"{rn['edge']:>9.4f} {rn['edge_method']:>20} "
            f"{(rn['fair_prob'] if rn['fair_prob'] is not None else 0):>8.4f}"
        )

    rec_off = sum(1 for r in off if r["status"] == RecommendationStatus.recommended.value)
    rec_on = sum(1 for r in on if r["status"] == RecommendationStatus.recommended.value)
    avg_edge_off = sum(r["edge"] for r in off) / len(off)
    avg_edge_on = sum(r["edge"] for r in on) / len(on)
    print("-" * 100)
    print(f"recommended:  off={rec_off}  on={rec_on}")
    print(f"avg edge:     off={avg_edge_off:.4f}  on={avg_edge_on:.4f}")
    print(f"edge delta (on-off, mean): {avg_edge_on - avg_edge_off:+.4f}")

    # Invariants that must hold for a safe rollout.
    assert all(r["edge_method"] == "market_average" for r in off), "disabled path must use legacy edge"
    assert all(r["fair_prob"] is None for r in off), "disabled path must not populate fair probability"
    assert any(r["edge_method"] == "sharp_anchor_power" for r in on), "enabled path must use sharp anchor when Pinnacle present"
    on_anchor = [r for r in on if r["edge_method"] == "sharp_anchor_power"]
    assert all(r["fair_prob"] is not None and 0.0 < r["fair_prob"] < 1.0 for r in on_anchor), (
        "anchor rows must carry a valid fair probability"
    )

    print("\ndevig backtest comparison passed (see table above to decide on enabling)")


if __name__ == "__main__":
    main()
