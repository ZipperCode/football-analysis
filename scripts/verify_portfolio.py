from __future__ import annotations

from datetime import datetime, timezone

from football_analysis.models import Match, MarketType, Recommendation, RecommendationStatus
from football_analysis.portfolio import apply_portfolio_constraints
from football_analysis.settings import PortfolioSettings


def main() -> None:
    config = PortfolioSettings(
        max_daily_exposure_fraction=0.0012,
        correlation_penalty_same_league=0.7,
        correlation_penalty_same_match=1.0,
        max_correlated_stakes_per_day=3,
    )
    bankroll_units = 1000
    matches = {
        "m1": _match("m1", "EPL"),
        "m2": _match("m2", "SERIE_A"),
        "m3": _match("m3", "LALIGA"),
        "m4": _match("m4", "EPL"),
    }

    limited = apply_portfolio_constraints(
        [_rec("r1", "m1", 0.6, 90), _rec("r2", "m2", 0.6, 88), _rec("r3", "m3", 0.6, 86)],
        matches,
        config,
        bankroll_units=bankroll_units,
    )
    assert [item.stake_units for item in limited] == [0.6, 0.6, 0.0], "daily budget should allow only 1.2u"
    assert limited[2].status is RecommendationStatus.paper_candidate
    assert "portfolio_daily_exposure_limit" in limited[2].risk_tags

    correlated = apply_portfolio_constraints(
        [_rec("r4", "m1", 0.6, 90), _rec("r5", "m4", 0.6, 88)],
        matches,
        config,
        bankroll_units=bankroll_units,
    )
    assert correlated[0].stake_units == 0.6
    assert correlated[1].stake_units == 0.18, f"expected same-league penalty, got {correlated[1].stake_units}"
    assert correlated[1].score_breakdown["live_gate"]["portfolio_adjusted"] is True

    same_match = apply_portfolio_constraints(
        [
            _rec("r6", "m1", 0.4, 90, selection="HOME"),
            _rec("r7", "m1", 0.4, 89, selection="OVER_2_5"),
        ],
        matches,
        config,
        bankroll_units=bankroll_units,
    )
    assert same_match[0].stake_units == 0.4
    assert same_match[1].stake_units == 0.0
    assert "portfolio_same_match_exclusion" in same_match[1].risk_tags

    print("portfolio verification passed")


def _match(match_id: str, league: str) -> Match:
    return Match(
        id=match_id,
        league=league,
        home_team=f"{match_id}-home",
        away_team=f"{match_id}-away",
        kickoff_at=datetime(2026, 6, 16, 19, 45, tzinfo=timezone.utc),
        data_completeness=0.9,
    )


def _rec(
    rec_id: str,
    match_id: str,
    stake_units: float,
    value_score: float,
    *,
    selection: str = "HOME",
) -> Recommendation:
    live_gate = {"passed": True, "applied_stake_units": stake_units}
    return Recommendation(
        id=rec_id,
        match_id=match_id,
        market_type=MarketType.one_x_two,
        selection=selection,
        status=RecommendationStatus.recommended,
        value_score=value_score,
        risk_score=20.0,
        confidence=0.8,
        stake_units=stake_units,
        odds_basis={"live_gate": live_gate},
        score_breakdown={"live_gate": live_gate},
        risk_tags=[],
        reason="test recommendation.",
        risk_notice="test",
    )


if __name__ == "__main__":
    main()
