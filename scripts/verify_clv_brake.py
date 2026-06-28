from __future__ import annotations

from datetime import datetime, timezone

from football_analysis.live_gate import _rolling_clv, apply_live_gate
from football_analysis.models import BetLog, Match, OddsSnapshot, Recommendation, RecommendationStatus
from football_analysis.settings import StrategyProfileSettings, load_settings


def _profile() -> StrategyProfileSettings:
    return StrategyProfileSettings(
        id="i1_middle_ah_away_live_long_horizon",
        name="I1 middle-season AH away live candidate",
        league_code="I1",
        market_type="asian_handicap",
        selections=["AH_AWAY"],
        season_phases=["middle"],
        stability_label="live_candidate",
        roi=0.189,
        settled_bets=231,
        positive_folds=13,
        fold_count=16,
        average_clv=0.0154,
        active=True,
        live_enabled=True,
        max_stake_units=0.4,
        long_horizon_roi=0.189,
        long_horizon_settled_bets=231,
        holdout_roi=0.194,
        holdout_settled_bets=122,
        holdout_positive_seasons=5,
        holdout_season_count=7,
        worst_season_roi=-0.1737,
    )


def _match() -> Match:
    return Match(
        id="clv-candidate",
        league="Italy - Serie A",
        home_team="CLV Home",
        away_team="CLV Away",
        kickoff_at=datetime(2026, 1, 17, 19, 45),
        data_completeness=0.92,
    )


def _recommendation() -> Recommendation:
    return Recommendation(
        id="clv-candidate-asian_handicap-AWAY-v1",
        match_id="clv-candidate",
        market_type="asian_handicap",
        selection="AWAY",
        status=RecommendationStatus.recommended,
        value_score=76.0,
        risk_score=30.0,
        confidence=0.7,
        stake_units=0.4,
        odds_basis={
            "best_price": 2.12,
            "market_average": 1.94,
            "edge": 0.0928,
            "strategy_profile": {"matched": True, "id": "i1_middle_ah_away_live_long_horizon"},
        },
        score_breakdown={
            "strategy_profile": {"matched": True, "id": "i1_middle_ah_away_live_long_horizon"},
        },
        reason="CLV brake fixture.",
        risk_notice="fixture",
    )


def _odds() -> list[OddsSnapshot]:
    now = datetime.now(timezone.utc)
    return [
        OddsSnapshot(
            id=f"clv-candidate-odds-{i}",
            match_id="clv-candidate",
            market_type="asian_handicap",
            line="+0.5",
            source="odds_api_io",
            bookmaker=bookmaker,
            outcome_odds={"AWAY": 2.12},
            market_average={"AWAY": 1.94},
            best_price={"AWAY": 2.12},
            movement=0.018,
            collected_at=now,
        )
        for i, bookmaker in enumerate(["Bet365", "Pinnacle"])
    ]


def _bets(placed_odds: float, closing_odds: float, count: int = 6) -> list[BetLog]:
    """Settled bets that all won (no rolling-loss/streak brake) with the given CLV."""
    return [
        BetLog(
            id=f"clv-bet-{i}",
            match_id=f"clv-bet-match-{i}",
            market_type="asian_handicap",
            selection="AH_AWAY(+0.5)",
            odds=placed_odds,
            stake_units=1.0,
            platform="paper",
            placed_at=datetime(2026, 1, 1 + i, 12, 0),
            result="win",
            profit_units=placed_odds - 1.0,
            closing_odds=closing_odds,
        )
        for i in range(count)
    ]


def main() -> None:
    # _rolling_clv math: placed 2.0, closing 2.10 -> CLV = 2.0/2.10 - 1 = -0.047619.
    neg = _rolling_clv(_bets(2.0, 2.10), load_settings())
    assert neg["observations"] == 6, neg
    assert abs(neg["average_clv"] - (2.0 / 2.10 - 1.0)) < 1e-6, neg

    base = load_settings()
    base.app.fixture_mode = False
    base.strategy_profiles = [_profile()]
    # Isolate the CLV brake from rolling-loss/streak brakes (all bets win above).
    base.live_trading.min_rolling_settled_bets = 99
    base.live_trading.max_recent_consecutive_losses = 99

    # 1) Brake disabled (default): negative CLV does NOT pause.
    disabled = apply_live_gate(_recommendation(), _match(), _odds(), _bets(2.0, 2.10), base)
    assert disabled.status is RecommendationStatus.recommended, disabled.risk_tags
    assert not any(tag.startswith("live_rolling_clv:") for tag in disabled.risk_tags), disabled.risk_tags
    # Payload still reports the observed rolling CLV for transparency.
    assert disabled.score_breakdown["live_gate"]["rolling_clv_observations"] == 6

    on = base.model_copy(deep=True)
    on.live_trading.rolling_clv_brake_enabled = True
    on.live_trading.min_rolling_clv_settled_bets = 5
    on.live_trading.min_rolling_clv = 0.0

    # 2) Brake enabled + negative rolling CLV -> pause live staking.
    paused = apply_live_gate(_recommendation(), _match(), _odds(), _bets(2.0, 2.10), on)
    assert paused.status is RecommendationStatus.paper_candidate, "negative rolling CLV must pause live staking"
    assert any(tag.startswith("live_rolling_clv:") for tag in paused.risk_tags), paused.risk_tags
    assert paused.stake_units == 0.0

    # 3) Brake enabled + positive rolling CLV -> still recommended.
    healthy = apply_live_gate(_recommendation(), _match(), _odds(), _bets(2.20, 2.05), on)
    assert healthy.status is RecommendationStatus.recommended, healthy.risk_tags
    assert not any(tag.startswith("live_rolling_clv:") for tag in healthy.risk_tags), healthy.risk_tags

    # 4) Brake enabled but insufficient sample -> no brake (need >= min_rolling_clv_settled_bets).
    thin = apply_live_gate(_recommendation(), _match(), _odds(), _bets(2.0, 2.10, count=3), on)
    assert thin.status is RecommendationStatus.recommended, "thin CLV sample must not trip the brake"

    print("clv brake verification passed")


if __name__ == "__main__":
    main()
