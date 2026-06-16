from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from football_analysis.db import StructuredRepository
from football_analysis.live_audit import audit_live_trading
from football_analysis.models import AgentFinding, BetLog, Match, OddsSnapshot
from football_analysis.scoring import score_match
from football_analysis.settings import StrategyProfileSettings, load_settings


def main() -> None:
    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'verify.db'}"
        settings.strategy_profiles = [_live_i1_profile()]
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            _insert_live_candidate(repository, "audit-live-pass", bookmaker_count=2)
            _insert_live_candidate(repository, "audit-single-bookmaker", bookmaker_count=1)

            default_scope = audit_live_trading(
                repository,
                settings,
                checked_at=datetime(2027, 1, 17, 20, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            )
            assert default_scope.status == "no_matches", "default live audit must exclude already-started matches"
            assert default_scope.total_matches == 0, "past kickoff matches must not stay in live audit scope"
            assert default_scope.recommended_count == 0, "past kickoff matches must not produce live recommendations"

            report = audit_live_trading(repository, settings, include_past=True)
            assert report.status == "ready", "one qualified live candidate should make audit ready"
            assert report.recommended_count == 1, "expected one real-money recommendation"
            assert report.paper_candidate_count == 1, "expected one gated paper candidate"
            assert report.total_live_stake_units == 0.4, "expected 0.4u total live exposure"
            assert report.gate_counts["live_min_bookmakers:1/2"] == 1, "single bookmaker gate must be counted"
            assert report.items[0].live_gate_passed is True, "ready item should be first"

            line_match = repository.get_model("matches", "audit-live-pass", Match)
            assert line_match is not None
            line_recommendation = score_match(
                line_match,
                [snapshot for snapshot in repository.list_models("odds", OddsSnapshot) if snapshot.match_id == line_match.id],
                [finding for finding in repository.list_models("findings", AgentFinding) if finding.match_id == line_match.id],
                settings,
            )
            assert line_recommendation.market_type == "asian_handicap"
            assert line_recommendation.odds_basis["line"] == "+0.5"

            for index in range(3):
                repository.upsert_model(
                    "bets",
                    f"audit-loss-{index}",
                    BetLog(
                        id=f"audit-loss-{index}",
                        match_id=f"audit-loss-match-{index}",
                        market_type="asian_handicap",
                        selection="AH_AWAY(+0.5)",
                        odds=1.95,
                        stake_units=0.4,
                        platform="paper",
                        placed_at=datetime(2026, 12, 20 + index, 12, 0),
                        result="loss",
                        profit_units=-0.4,
                    ),
                )

            paused = audit_live_trading(repository, settings, include_past=True)
            assert paused.status == "paused", "recent loss streak must pause the live audit"
            assert paused.recommended_count == 0, "paused audit must not retain real-money recommendations"
            assert paused.total_live_stake_units == 0.0, "paused audit must zero live exposure"
            assert paused.recent_consecutive_losses == 3, "pause reason must expose loss streak"
            assert "live_recent_consecutive_losses:3/3" in paused.issues
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
            _insert_live_candidate(repository, "audit-rolling-drawdown", bookmaker_count=2)
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
                    f"audit-rolling-result-{index}",
                    BetLog(
                        id=f"audit-rolling-result-{index}",
                        match_id=f"audit-rolling-result-match-{index}",
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

            rolling_paused = audit_live_trading(repository, settings, include_past=True)
            assert rolling_paused.status == "paused", "rolling drawdown must pause the live audit"
            assert rolling_paused.recommended_count == 0, "rolling pause must remove real-money recommendations"
            assert "live_rolling_loss_units:2.00/2.00" in rolling_paused.issues
            assert rolling_paused.gate_counts["live_rolling_loss_units:2.00/2.00"] == 1
        finally:
            repository.close()

    print("live audit verification passed")


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


def _insert_live_candidate(repository: StructuredRepository, match_id: str, bookmaker_count: int) -> None:
    kickoff = datetime(2027, 1, 17, 19, 45, tzinfo=ZoneInfo("Asia/Shanghai"))
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


if __name__ == "__main__":
    main()
