from __future__ import annotations

from datetime import datetime, timezone

from football_analysis.models import MarketType, Match, OddsSnapshot
from football_analysis.scoring import score_match
from football_analysis.settings import load_settings


def _match() -> Match:
    return Match(
        id="m1",
        league="EPL",
        home_team="Home FC",
        away_team="Away FC",
        kickoff_at=datetime(2026, 6, 16, 19, 45, tzinfo=timezone.utc),
        data_completeness=0.95,
    )


def _snapshot(bookmaker: str, odds: dict[str, float]) -> OddsSnapshot:
    return OddsSnapshot(
        id=f"{bookmaker}-1x2",
        match_id="m1",
        market_type=MarketType.one_x_two,
        source=f"src-{bookmaker.lower().replace(' ', '_')}",
        bookmaker=bookmaker,
        outcome_odds=dict(odds),
    )


def _aggregate(snapshots: list[OddsSnapshot]) -> list[OddsSnapshot]:
    selections = sorted({s for snap in snapshots for s in snap.outcome_odds})
    averages = {
        sel: round(sum(snap.outcome_odds[sel] for snap in snapshots if sel in snap.outcome_odds)
                   / sum(1 for snap in snapshots if sel in snap.outcome_odds), 4)
        for sel in selections
    }
    best = {sel: max(snap.outcome_odds[sel] for snap in snapshots if sel in snap.outcome_odds) for sel in selections}
    return [snap.model_copy(update={"market_average": averages, "best_price": best}) for snap in snapshots]


def main() -> None:
    match = _match()
    pinnacle = _snapshot("Pinnacle", {"HOME": 2.05, "DRAW": 3.5, "AWAY": 3.7})
    soft = _snapshot("BetSoft", {"HOME": 2.30, "DRAW": 3.3, "AWAY": 3.6})
    with_sharp = _aggregate([pinnacle, soft])
    without_sharp = _aggregate([soft, _snapshot("BetOther", {"HOME": 2.25, "DRAW": 3.2, "AWAY": 3.5})])

    # 1) devig disabled (default) -> legacy market_average edge_method (regression guard).
    settings = load_settings()
    assert settings.devig.enabled is False, "default config must keep devig disabled"
    rec_off = score_match(match, with_sharp, [], settings)
    assert rec_off.odds_basis["edge_method"] == "market_average", rec_off.odds_basis["edge_method"]
    assert rec_off.odds_basis["fair_probability"] is None, rec_off.odds_basis["fair_probability"]

    # 2) devig enabled + Pinnacle present -> sharp_anchor edge with fair probability.
    settings_on = settings.model_copy(update={"devig": settings.devig.model_copy(update={"enabled": True})})
    rec_on = score_match(match, with_sharp, [], settings_on)
    assert rec_on.odds_basis["edge_method"] == "sharp_anchor_power", rec_on.odds_basis["edge_method"]
    assert rec_on.odds_basis["fair_probability"] is not None, rec_on.odds_basis
    assert rec_on.odds_basis["anchor_bookmaker"] == "Pinnacle", rec_on.odds_basis["anchor_bookmaker"]

    # 3) devig enabled but no sharp book -> graceful fallback to market_average.
    rec_fallback = score_match(match, without_sharp, [], settings_on)
    assert rec_fallback.odds_basis["edge_method"] == "market_average", rec_fallback.odds_basis["edge_method"]

    print("scoring devig verification passed")


if __name__ == "__main__":
    main()
