from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from football_analysis.db import StructuredRepository
from football_analysis.live_gate import apply_live_gate
from football_analysis.models import AgentFinding, BetLog, Match, OddsSnapshot, Recommendation, RecommendationStatus
from football_analysis.service import AnalysisService
from football_analysis.settings import StrategyProfileSettings, load_settings


def main() -> None:
    assert callable(apply_live_gate), "live gate must expose an explicit apply function"
    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'verify.db'}"
        settings.strategy_profiles = [_live_i1_profile()]
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)
            _insert_live_candidate(repository, "live-pass", bookmaker_count=2)
            passed = service.analyze_match("live-pass").recommendation
            assert passed.status is RecommendationStatus.recommended, "qualified live candidate must stay recommended"
            assert passed.stake_units == 0.4, "profile cap must limit stake to 0.4u"
            assert passed.score_breakdown["live_gate"]["passed"] is True, "live gate payload must pass"
            assert passed.score_breakdown["live_gate"]["profile_id"] == "i1_middle_ah_away_live_long_horizon"

            _insert_live_candidate(repository, "single-bookmaker", bookmaker_count=1)
            single_bookmaker = service.analyze_match("single-bookmaker").recommendation
            assert single_bookmaker.status is RecommendationStatus.paper_candidate, "single-bookmaker odds must not enter live picks"
            assert single_bookmaker.stake_units == 0.0, "failed live gate must zero stake"
            assert "live_min_bookmakers:1/2" in single_bookmaker.risk_tags

            _insert_live_candidate(repository, "wrong-phase", bookmaker_count=2, kickoff=datetime(2026, 5, 9, 19, 45))
            wrong_phase = service.analyze_match("wrong-phase").recommendation
            assert wrong_phase.status is RecommendationStatus.paper_candidate, "middle-season profile must not stake late-season matches"
            assert "live_profile_season_phase:late_not_in:middle" in wrong_phase.risk_tags

            _insert_live_candidate(
                repository,
                "stale-odds",
                bookmaker_count=2,
                collected_at=datetime.now(timezone.utc) - timedelta(minutes=240),
            )
            stale = service.analyze_match("stale-odds").recommendation
            assert stale.status is RecommendationStatus.paper_candidate, "stale odds must not enter live picks"
            assert any(tag.startswith("live_max_odds_age_minutes:") for tag in stale.risk_tags)
            assert stale.score_breakdown["live_gate"]["odds_age_minutes"] >= 230

            for index in range(3):
                repository.upsert_model(
                    "bets",
                    f"recent-loss-{index}",
                    BetLog(
                        id=f"recent-loss-{index}",
                        match_id=f"recent-loss-match-{index}",
                        market_type="asian_handicap",
                        selection="AH_AWAY(+0.5)",
                        odds=1.95,
                        stake_units=0.4,
                        platform="paper",
                        placed_at=datetime(2026, 1, 8 + index, 12, 0),
                        result="loss",
                        profit_units=-0.4,
                    ),
                )
            _insert_live_candidate(repository, "paused-after-losses", bookmaker_count=2)
            paused = service.analyze_match("paused-after-losses").recommendation
            assert paused.status is RecommendationStatus.paper_candidate, "recent loss streak must pause live staking"
            assert "live_recent_consecutive_losses:3/3" in paused.risk_tags
        finally:
            repository.close()

    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'verify-rolling.db'}"
        settings.strategy_profiles = [_live_i1_profile()]
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)
            rolling_results = [
                ("loss", -1.0),
                ("win", 0.5),
                ("loss", -1.0),
                ("loss", -1.0),
                ("win", 0.5),
            ]
            for index, (result, profit) in enumerate(rolling_results):
                repository.upsert_model(
                    "bets",
                    f"rolling-result-{index}",
                    BetLog(
                        id=f"rolling-result-{index}",
                        match_id=f"rolling-result-match-{index}",
                        market_type="asian_handicap",
                        selection="AH_AWAY(+0.5)",
                        odds=2.0,
                        stake_units=1.0,
                        platform="paper",
                        placed_at=datetime(2026, 1, 1 + index, 12, 0),
                        result=result,
                        profit_units=profit,
                    ),
                )
            _insert_live_candidate(repository, "paused-after-rolling-drawdown", bookmaker_count=2)
            rolling_paused = service.analyze_match("paused-after-rolling-drawdown").recommendation
            assert rolling_paused.status is RecommendationStatus.paper_candidate, (
                "rolling drawdown must pause live staking even without a current loss streak"
            )
            assert "live_rolling_loss_units:2.00/2.00" in rolling_paused.risk_tags
        finally:
            repository.close()

    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'verify-profile-review.db'}"
        settings.strategy_profiles = [_live_i1_profile()]
        settings.live_trading.min_rolling_settled_bets = 99
        settings.live_trading.max_recent_consecutive_losses = 99
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)
            _insert_negative_profile_sample(repository)
            _insert_live_candidate(repository, "paused-after-profile-review", bookmaker_count=2)
            profile_paused = service.analyze_match("paused-after-profile-review").recommendation
            assert profile_paused.status is RecommendationStatus.paper_candidate, (
                "live-review pause_live action must block the next live stake for that profile"
            )
            assert "live_profile_review_action:pause_live" in profile_paused.risk_tags
        finally:
            repository.close()

    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'verify-review-cache.db'}"
        settings.strategy_profiles = [_live_i1_profile()]
        settings.live_trading.min_rolling_settled_bets = 99
        settings.live_trading.max_recent_consecutive_losses = 99
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)
            _insert_live_candidate(repository, "cache-before-review-losses", bookmaker_count=2)
            before_review = service.analyze_match("cache-before-review-losses").recommendation
            assert before_review.status is RecommendationStatus.recommended, "initial qualified candidate must pass"

            _insert_negative_profile_sample_with_service(repository, service)
            _insert_live_candidate(repository, "cache-after-review-losses", bookmaker_count=2)
            after_review = service.analyze_match("cache-after-review-losses").recommendation
            assert after_review.status is RecommendationStatus.paper_candidate, (
                "recording settled losses through the service must refresh profile-review live gate actions"
            )
            assert "live_profile_review_action:pause_live" in after_review.risk_tags
        finally:
            repository.close()

    settings = load_settings()
    settings.app.fixture_mode = False
    secondary_match = Match(
        id="secondary-live-pass",
        league="Brazil - Brasileiro Serie A",
        home_team="Secondary Home",
        away_team="Secondary Away",
        kickoff_at=datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc),
        data_completeness=0.92,
    )
    secondary_recommendation = Recommendation(
        id="secondary-live-pass-1x2-HOME-v1",
        match_id=secondary_match.id,
        market_type="1x2",
        selection="HOME",
        status=RecommendationStatus.recommended,
        value_score=76.0,
        risk_score=30.0,
        confidence=0.7,
        stake_units=0.5,
        odds_basis={
            "best_price": 2.12,
            "market_average": 1.95,
            "edge": 0.0872,
            "strategy_profile": {"matched": False},
            "strategy_confidence_class": "secondary_live_small_stake",
            "tier_policy": {"passed": True, "label": "secondary_live_small_stake"},
        },
        score_breakdown={
            "strategy_profile": {"matched": False},
            "strategy_confidence_class": "secondary_live_small_stake",
            "tier_policy": {"passed": True, "label": "secondary_live_small_stake"},
        },
        reason="Secondary league live-scoring candidate.",
        risk_notice=settings.app.risk_notice,
    )
    secondary_gate = apply_live_gate(
        secondary_recommendation,
        secondary_match,
        _secondary_live_odds(secondary_match.id),
        [],
        settings,
    )
    assert secondary_gate.status is RecommendationStatus.recommended, (
        "secondary_live_small_stake candidates that passed tier policy should not require a strategy profile"
    )
    assert secondary_gate.score_breakdown["live_gate"]["passed"] is True
    assert "live_missing_strategy_profile" not in secondary_gate.risk_tags

    secondary_tier_threshold_match = secondary_match.model_copy(
        update={
            "id": "secondary-tier-threshold-pass",
            "data_completeness": 0.76,
        }
    )
    secondary_tier_threshold_recommendation = secondary_recommendation.model_copy(
        update={
            "id": "secondary-tier-threshold-pass-1x2-HOME-v1",
            "match_id": secondary_tier_threshold_match.id,
            "risk_score": 44.0,
            "confidence": 0.60,
            "score_breakdown": {
                "strategy_profile": {"matched": False},
                "strategy_confidence_class": "secondary_live_small_stake",
                "tier_policy": {
                    "passed": True,
                    "label": "secondary_live_small_stake",
                    "min_data_quality": 0.75,
                    "max_risk_score": 45.0,
                    "min_confidence": 0.58,
                    "min_bookmakers": 2,
                },
            },
            "odds_basis": {
                "best_price": 2.12,
                "market_average": 1.95,
                "edge": 0.0872,
                "strategy_profile": {"matched": False},
                "strategy_confidence_class": "secondary_live_small_stake",
                "tier_policy": {
                    "passed": True,
                    "label": "secondary_live_small_stake",
                    "min_data_quality": 0.75,
                    "max_risk_score": 45.0,
                    "min_confidence": 0.58,
                    "min_bookmakers": 2,
                },
            },
        }
    )
    secondary_tier_threshold_gate = apply_live_gate(
        secondary_tier_threshold_recommendation,
        secondary_tier_threshold_match,
        _secondary_live_odds(secondary_tier_threshold_match.id),
        [],
        settings,
    )
    assert secondary_tier_threshold_gate.status is RecommendationStatus.recommended, (
        "secondary live tier thresholds should govern profileless small-stake candidates"
    )
    assert "live_min_data_quality:0.76/0.82" not in secondary_tier_threshold_gate.risk_tags
    assert "live_max_risk_score:44.00/42.00" not in secondary_tier_threshold_gate.risk_tags

    print("live gate verification passed")


def _live_i1_profile() -> StrategyProfileSettings:
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


def _insert_live_candidate(
    repository: StructuredRepository,
    match_id: str,
    bookmaker_count: int,
    kickoff: datetime | None = None,
    collected_at: datetime | None = None,
) -> None:
    kickoff = kickoff or datetime(2026, 1, 17, 19, 45) + timedelta(minutes=len(match_id))
    repository.upsert_model(
        "matches",
        match_id,
        Match(
            id=match_id,
            league="Italy - Serie A",
            home_team=f"{match_id} Home",
            away_team=f"{match_id} Away",
            kickoff_at=kickoff,
            data_completeness=0.92,
        ),
    )
    for index, bookmaker in enumerate(["Bet365", "Pinnacle"][:bookmaker_count]):
        repository.upsert_model(
            "odds",
            f"{match_id}-odds-{index}",
            OddsSnapshot(
                id=f"{match_id}-odds-{index}",
                match_id=match_id,
                market_type="asian_handicap",
                line="+0.5",
                source="odds_api_io",
                bookmaker=bookmaker,
                outcome_odds={"AWAY": 2.12},
                market_average={"AWAY": 1.94},
                best_price={"AWAY": 2.12},
                movement=0.018,
                collected_at=collected_at or datetime.now(timezone.utc),
            ),
        )
    repository.upsert_model(
        "findings",
        f"{match_id}-history",
        AgentFinding(
            id=f"{match_id}-history",
            match_id=match_id,
            agent_name="History Agent",
            summary="Long-horizon profile and matchup context agree with away handicap value.",
            confidence=0.78,
            score_delta=9.0,
        ),
    )


def _secondary_live_odds(match_id: str) -> list[OddsSnapshot]:
    collected_at = datetime.now(timezone.utc)
    return [
        OddsSnapshot(
            id=f"{match_id}-{bookmaker}",
            match_id=match_id,
            market_type="1x2",
            source="odds_api_io",
            bookmaker=bookmaker,
            outcome_odds={"HOME": price},
            market_average={"HOME": 1.95},
            best_price={"HOME": 2.12},
            movement=0.02,
            collected_at=collected_at,
        )
        for bookmaker, price in [("Bet365", 2.08), ("Pinnacle", 2.12)]
    ]


def _insert_negative_profile_sample(repository: StructuredRepository) -> None:
    kickoff = datetime(2025, 12, 1, 19, 45)
    profits = [-1.0, -1.0, -1.0, -1.0, 0.8, 0.6]
    for index, profit in enumerate(profits):
        match_id = f"profile-review-loss-{index}"
        repository.upsert_model(
            "matches",
            match_id,
            Match(
                id=match_id,
                league="Italy - Serie A",
                home_team=f"Profile Review Home {index}",
                away_team=f"Profile Review Away {index}",
                kickoff_at=kickoff + timedelta(days=index),
                data_completeness=0.92,
                home_score=1,
                away_score=1,
            ),
        )
        repository.upsert_model(
            "recommendations",
            f"{match_id}-asian_handicap-AWAY-v1",
            Recommendation(
                id=f"{match_id}-asian_handicap-AWAY-v1",
                match_id=match_id,
                market_type="asian_handicap",
                selection="AWAY",
                status=RecommendationStatus.recommended,
                value_score=75.0,
                risk_score=30.0,
                confidence=0.7,
                stake_units=0.4,
                odds_basis={
                    "best_price": 2.12,
                    "strategy_profile": {
                        "matched": True,
                        "id": "i1_middle_ah_away_live_long_horizon",
                    },
                },
                score_breakdown={
                    "strategy_profile": {
                        "matched": True,
                        "id": "i1_middle_ah_away_live_long_horizon",
                    }
                },
                reason="profile review fixture",
                risk_notice="profile review fixture",
            ),
        )
        repository.upsert_model(
            "bets",
            f"profile-review-bet-{index}",
            BetLog(
                id=f"profile-review-bet-{index}",
                match_id=match_id,
                market_type="asian_handicap",
                selection="AH_AWAY(+0.5)",
                odds=2.10,
                stake_units=1.0,
                platform="paper",
                placed_at=kickoff + timedelta(days=index, hours=-2),
                result="win" if profit > 0 else "loss",
                profit_units=profit,
                closing_odds=2.25,
            ),
        )


def _insert_negative_profile_sample_with_service(
    repository: StructuredRepository,
    service: AnalysisService,
) -> None:
    kickoff = datetime(2025, 12, 1, 19, 45)
    profits = [-1.0, -1.0, -1.0, -1.0, 0.8, 0.6]
    for index, profit in enumerate(profits):
        match_id = f"profile-review-cache-loss-{index}"
        repository.upsert_model(
            "matches",
            match_id,
            Match(
                id=match_id,
                league="Italy - Serie A",
                home_team=f"Profile Review Cache Home {index}",
                away_team=f"Profile Review Cache Away {index}",
                kickoff_at=kickoff + timedelta(days=index),
                data_completeness=0.92,
                home_score=1,
                away_score=1,
            ),
        )
        repository.upsert_model(
            "recommendations",
            f"{match_id}-asian_handicap-AWAY-v1",
            Recommendation(
                id=f"{match_id}-asian_handicap-AWAY-v1",
                match_id=match_id,
                market_type="asian_handicap",
                selection="AWAY",
                status=RecommendationStatus.recommended,
                value_score=75.0,
                risk_score=30.0,
                confidence=0.7,
                stake_units=0.4,
                odds_basis={
                    "best_price": 2.12,
                    "strategy_profile": {
                        "matched": True,
                        "id": "i1_middle_ah_away_live_long_horizon",
                    },
                },
                score_breakdown={
                    "strategy_profile": {
                        "matched": True,
                        "id": "i1_middle_ah_away_live_long_horizon",
                    }
                },
                reason="profile review cache fixture",
                risk_notice="profile review cache fixture",
            ),
        )
        service.record_bet(
            BetLog(
                id=f"profile-review-cache-bet-{index}",
                match_id=match_id,
                market_type="asian_handicap",
                selection="AH_AWAY(+0.5)",
                odds=2.10,
                stake_units=1.0,
                platform="paper",
                placed_at=kickoff + timedelta(days=index, hours=-2),
                result="win" if profit > 0 else "loss",
                profit_units=profit,
                closing_odds=2.25,
            )
        )


if __name__ == "__main__":
    main()
