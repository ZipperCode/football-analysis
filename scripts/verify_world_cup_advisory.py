from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory

from football_analysis.db import StructuredRepository
from football_analysis.models import AgentFinding, EvidenceSource, Match, OddsSnapshot, RecommendationStatus
from football_analysis.production import build_production_execution_queue
from football_analysis.service import AnalysisService
from football_analysis.settings import LeagueSettings, StrategyProfileSettings, load_settings
from football_analysis.world_cup import recommend_world_cup


def main() -> None:
    with TemporaryDirectory() as tmpdir:
        repository = StructuredRepository(f"sqlite:///{tmpdir}/verify.db")
        repository.initialize()
        try:
            settings = load_settings()
            settings.leagues = [
                LeagueSettings(
                    code="WORLD_CUP",
                    name="FIFA World Cup",
                    country="World",
                    aliases=["International - FIFA World Cup"],
                    tier="major_tournament",
                    analysis_depth="deep",
                    strategy_mode="paper",
                    min_bookmakers=2,
                    paper_only=True,
                )
            ]
            settings.live_trading.min_rolling_settled_bets = 99
            service = AnalysisService(settings, repository)
            kickoff_at = datetime.now(timezone.utc) + timedelta(hours=8)
            match = _store_world_cup_match(repository, kickoff_at=kickoff_at, data_completeness=0.78)
            _store_1x2_odds(repository, match.id, kickoff_at=kickoff_at, away_prices=[1.21, 1.22, 1.24])
            _store_research(repository, match.id, confidence=0.78)

            analysis = service.analyze_match(match.id)
            recommendation = analysis.recommendation
            live_gate = recommendation.score_breakdown.get("live_gate", {})

            assert recommendation.status is RecommendationStatus.advisory_recommended
            assert recommendation.selection == "AWAY"
            assert recommendation.stake_units == 0.0
            assert recommendation.odds_basis["best_price"] < 2.0
            assert recommendation.score_breakdown["research_advisory"]["evidence_count"] == 3
            assert "live_status_not_recommended:advisory_recommended" in live_gate["gates_failed"]

            coverage_match = Match(
                id="verify-world-cup-coverage-only",
                league="FIFA World Cup",
                home_team="Spain",
                away_team="Cape Verde",
                kickoff_at=kickoff_at + timedelta(hours=4),
                data_completeness=0.76,
            )
            repository.upsert_model("matches", coverage_match.id, coverage_match)
            for index, bookmaker in enumerate(["Book A", "Book B"], start=1):
                repository.upsert_model(
                    "odds",
                    f"verify-world-cup-coverage-only-odds-{index}",
                    OddsSnapshot(
                        id=f"verify-world-cup-coverage-only-odds-{index}",
                        match_id=coverage_match.id,
                        market_type="over_under",
                        line="2.5",
                        source="test",
                        bookmaker=bookmaker,
                        collected_at=datetime.now(timezone.utc),
                        outcome_odds={"OVER": 2.1 + index * 0.02},
                        market_average={"OVER": 1.94},
                        best_price={"OVER": 2.14},
                    ),
                )
            coverage = service.analyze_match(coverage_match.id).recommendation
            coverage_gate = coverage.score_breakdown.get("live_gate", {})

            assert coverage.status is RecommendationStatus.advisory_recommended
            assert coverage.stake_units == 0.0
            assert coverage.score_breakdown["coverage_advisory"]["matched"] is True
            assert "coverage_advisory" in coverage.risk_tags
            assert "live_status_not_recommended:advisory_recommended" in coverage_gate["gates_failed"]
        finally:
            repository.close()

    with TemporaryDirectory() as tmpdir:
        repository = StructuredRepository(f"sqlite:///{tmpdir}/verify-live.db")
        repository.initialize()
        old_env = os.environ.get("EXA_API_KEY")
        os.environ["EXA_API_KEY"] = "test-key"
        try:
            settings = load_settings()
            settings.leagues = [
                LeagueSettings(
                    code="WORLD_CUP",
                    name="FIFA World Cup",
                    country="World",
                    aliases=["International - FIFA World Cup"],
                    tier="major_tournament",
                    analysis_depth="deep",
                    strategy_mode="live",
                    min_bookmakers=2,
                    paper_only=False,
                )
            ]
            settings.strategy_profiles = [_world_cup_profile()]
            settings.live_trading.min_rolling_settled_bets = 99
            settings.live_trading.max_odds_age_minutes = 180
            settings.live_trading.max_daily_stake_units = 1.0
            service = AnalysisService(settings, repository)

            kickoff_at = datetime.now(timezone.utc) + timedelta(minutes=75)
            match = _store_world_cup_match(repository, kickoff_at=kickoff_at, data_completeness=0.88)
            _store_1x2_odds(repository, match.id, kickoff_at=kickoff_at, away_prices=[1.8, 1.86, 1.94])
            _store_research(repository, match.id, confidence=0.82, include_lineup=True)
            _store_qqsd_context(repository, match.id)

            ordinary = service.analyze_match(match.id).recommendation
            assert ordinary.status is RecommendationStatus.advisory_recommended
            assert ordinary.stake_units == 0.0
            assert "live_status_not_recommended:advisory_recommended" in ordinary.score_breakdown["live_gate"]["gates_failed"]

            advisory = recommend_world_cup(service, match_date=kickoff_at.astimezone(settings.app.tzinfo).date().isoformat(), stage="advisory")
            assert advisory["status"] == "advisory"
            assert advisory["recommendations"][0]["status"] == "advisory_recommended"
            assert any(
                issue.startswith("world_cup_advisory_window:verify-world-cup:")
                for issue in advisory["issues"]
            )

            final = recommend_world_cup(service, match_date=kickoff_at.astimezone(settings.app.tzinfo).date().isoformat(), stage="final")
            assert final["status"] == "ready", final["issues"]
            assert final["ignore_final_window"] is False
            final_pick = final["recommendations"][0]
            assert final_pick["status"] == "recommended"
            assert final_pick["stake_units"] in {0.25, 0.5}
            assert final_pick["score_breakdown"]["world_cup_high_winrate"]["passed"] is True
            assert final_pick["score_breakdown"]["world_cup_high_winrate"]["qqsd_data"]["available"] is True
            assert final_pick["score_breakdown"]["world_cup_high_winrate"]["qqsd_data"]["markets"]["asian_handicap"]["history_row_count"] == 8
            assert final_pick["score_breakdown"]["qqsd_evidence"]["lineup_quality"] == 3
            assert final_pick["score_breakdown"]["qqsd_evidence"]["value_delta"] > 0
            assert final_pick["score_breakdown"]["live_gate"]["passed"] is True

            outside_window_at = datetime.now(timezone.utc) + timedelta(hours=8)
            outside_match = _store_world_cup_match(
                repository,
                kickoff_at=outside_window_at,
                data_completeness=0.88,
                match_id="verify-world-cup-ignore-window",
            )
            _store_1x2_odds(repository, outside_match.id, kickoff_at=outside_window_at, away_prices=[1.8, 1.86, 1.94])
            _store_research(repository, outside_match.id, confidence=0.82, include_lineup=True)
            _store_qqsd_context(repository, outside_match.id)
            ignore_window = recommend_world_cup(
                service,
                match_date=outside_window_at.astimezone(settings.app.tzinfo).date().isoformat(),
                stage="final",
                ignore_final_window=True,
            )
            ignored_pick = next(
                item for item in ignore_window["recommendations"] if item["match_id"] == outside_match.id
            )
            ignored_gate = ignored_pick["score_breakdown"]["world_cup_high_winrate"]
            assert ignore_window["ignore_final_window"] is True
            assert ignored_gate["ignore_final_window"] is True
            assert not any(issue.startswith("world_cup_final_window:") for issue in ignored_gate["issues"])

            queue = build_production_execution_queue(
                service,
                include_past=False,
                platform="real",
                league_codes={"WORLD_CUP"},
            )
            assert queue["ready_to_execute"] is True, queue
            assert queue["queue_count"] >= 1
            item = next(item for item in queue["items"] if item["match_id"] == match.id)
            assert item["market_type"] == "1x2"
            assert item["selection"] == "AWAY"
            assert item["remaining_stake_units"] in {0.25, 0.5}
            assert item["minimum_execution_odds"] < item["approved_odds"]

            blocked_queue_repo = StructuredRepository(f"sqlite:///{tmpdir}/verify-blocked.db")
            blocked_queue_repo.initialize()
            try:
                blocked_service = AnalysisService(settings, blocked_queue_repo)
                blocked_match = _store_world_cup_match(blocked_queue_repo, kickoff_at=kickoff_at, data_completeness=0.88)
                _store_1x2_odds(blocked_queue_repo, blocked_match.id, kickoff_at=kickoff_at, away_prices=[1.8, 1.86, 1.94])
                _store_research(blocked_queue_repo, blocked_match.id, confidence=0.82, include_lineup=True)
                blocked_service.analyze_match(blocked_match.id)
                blocked_queue = build_production_execution_queue(
                    blocked_service,
                    include_past=False,
                    platform="real",
                    league_codes={"WORLD_CUP"},
                )
                assert blocked_queue["ready_to_execute"] is False
                assert blocked_queue["queue_count"] == 0
                assert "no_live_gate_passed_candidates" in blocked_queue["issues"]
            finally:
                blocked_queue_repo.close()
        finally:
            if old_env is None:
                os.environ.pop("EXA_API_KEY", None)
            else:
                os.environ["EXA_API_KEY"] = old_env
            repository.close()

    print("verify_world_cup_advisory: ok")


def _store_world_cup_match(
    repository: StructuredRepository,
    kickoff_at: datetime,
    data_completeness: float,
    match_id: str = "verify-world-cup",
) -> Match:
    match = Match(
        id=match_id,
        league="FIFA World Cup",
        home_team="Qatar",
        away_team="Switzerland",
        kickoff_at=kickoff_at,
        data_completeness=data_completeness,
    )
    repository.upsert_model("matches", match.id, match)
    return match


def _store_1x2_odds(repository: StructuredRepository, match_id: str, kickoff_at: datetime, away_prices: list[float]) -> None:
    for index, (bookmaker, away_price) in enumerate(
        zip(["Book A", "Book B", "Book C"], away_prices, strict=True),
        start=1,
    ):
        repository.upsert_model(
            "odds",
            f"{match_id}-odds-{index}",
            OddsSnapshot(
                id=f"{match_id}-odds-{index}",
                match_id=match_id,
                market_type="1x2",
                source="test",
                bookmaker=bookmaker,
                collected_at=datetime.now(timezone.utc),
                outcome_odds={"HOME": 4.6 + index * 0.05, "DRAW": 3.3, "AWAY": away_price},
                market_average={"HOME": 4.55, "DRAW": 3.3, "AWAY": sum(away_prices) / len(away_prices)},
                best_price={"HOME": 4.7, "DRAW": 3.35, "AWAY": max(away_prices)},
            ),
        )


def _store_research(
    repository: StructuredRepository,
    match_id: str,
    confidence: float,
    include_lineup: bool = False,
) -> None:
    summary = "Multiple independent previews support Switzerland to win."
    payload = {"advisory_recommendation": True, "market_type": "1x2", "selection": "AWAY"}
    if include_lineup:
        summary += " Team news and starting lineup context show no major injury concerns."
        payload.update({"lineup_verified": True, "injury_context": "no major injuries"})
    finding = AgentFinding(
        id=f"{match_id}-research",
        match_id=match_id,
        agent_name="Research Advisory Agent",
        summary=summary,
        evidence_sources=[
            EvidenceSource(title="Preview A", url="https://example.com/a", publisher="A"),
            EvidenceSource(title="Preview B", url="https://example.com/b", publisher="B"),
            EvidenceSource(title="Preview C", url="https://example.com/c", publisher="C"),
        ],
        confidence=confidence,
        score_delta=8.0,
        payload=payload,
    )
    repository.upsert_model("findings", finding.id, finding)


def _store_qqsd_context(repository: StructuredRepository, match_id: str) -> None:
    finding = AgentFinding(
        id=f"{match_id}:qqsd-context",
        match_id=match_id,
        agent_name="qqsd_full_context",
        summary="QQSD完整数据：盘口时间线、积分、欧赔摘要可用。",
        evidence_sources=[
            EvidenceSource(title="QQSD match detail", publisher="QQSD"),
            EvidenceSource(title="QQSD standings and team power", publisher="QQSD"),
            EvidenceSource(title="QQSD odds context", publisher="QQSD"),
            EvidenceSource(title="QQSD odds timeline", publisher="QQSD"),
        ],
        confidence=0.70,
        payload={
            "provider": "qqsd",
            "fid": match_id,
            "detail": {"fid": match_id, "hname": "Qatar", "aname": "Switzerland"},
            "standings": {"hpower": {"total_score": "91"}, "apower": {"total_score": "114"}},
            "match_context": {
                "injury_rows": 1,
                "h2h_rows": 3,
                "lineup_full": {
                    "home_shape": "4-2-3-1",
                    "away_shape": "3-4-2-1",
                    "home_starters": 11,
                    "away_starters": 11,
                },
            },
            "injury_preview": {"shangbing": {"home": [{"name": "rotation forward"}]}, "xinshui": "No major injury concern."},
            "lineup_full": {
                "home": {"zhenxing": "4-2-3-1", "shoufa": [{"name": str(i)} for i in range(11)]},
                "away": {"zhenxing": "3-4-2-1", "shoufa": [{"name": str(i)} for i in range(11)]},
            },
            "odds_context": {
                "europe_history_rows": 12,
                "summary_rows": 4,
                "same_odds_history": {"spf": {"count": "120", "winrate": "56%"}},
                "betting_distribution": {"tend": {"tradetend": "客胜热度稳定"}},
                "bifa_trade": {"amount": {"total": "4100"}},
                "odds_trend": {"euro": {"win": "3.80", "draw": "3.40", "lost": "1.94"}},
                "odds_change_rows": 5,
                "company_count": 25,
            },
            "odds_timeline": {
                "provider": "qqsd",
                "fid": match_id,
                "summary": {
                    "market_count": 3,
                    "history_available_count": 3,
                    "current_available_count": 3,
                    "history_coverage_rate": 1.0,
                    "current_coverage_rate": 1.0,
                },
                "markets": {
                    "1x2": {
                        "company": {"id": "8", "name": "Pinnacle平博"},
                        "current_available": True,
                        "history_row_count": 12,
                        "history_availability": "history_available",
                    },
                    "asian_handicap": {
                        "company": {"id": "8", "name": "Pinnacle平博"},
                        "current_available": True,
                        "history_row_count": 8,
                        "history_availability": "history_available",
                    },
                    "over_under": {
                        "company": {"id": "8", "name": "Pinnacle平博"},
                        "current_available": True,
                        "history_row_count": 9,
                        "history_availability": "history_available",
                    },
                },
            },
            "qqsd_errors": [],
        },
    )
    repository.upsert_model("findings", finding.id, finding)


def _world_cup_profile() -> StrategyProfileSettings:
    return StrategyProfileSettings(
        id="world_cup_high_winrate",
        name="World Cup high win-rate 1x2 controlled live profile",
        league_code="WORLD_CUP",
        market_type="1x2",
        selections=["HOME", "AWAY"],
        season_phases=["all"],
        stability_label="high_winrate_controlled_live",
        roi=0.082,
        hit_rate=0.672,
        settled_bets=168,
        positive_folds=4,
        fold_count=5,
        average_clv=0.018,
        live_enabled=True,
        max_stake_units=0.5,
        long_horizon_roi=0.082,
        long_horizon_settled_bets=168,
        holdout_roi=0.091,
        holdout_settled_bets=96,
        holdout_positive_seasons=4,
        holdout_season_count=5,
        worst_season_roi=-0.11,
        max_drawdown_units=3.4,
        sample_scope=["WORLD_CUP", "EURO", "COPA_AMERICA", "ASIAN_CUP", "AFCON"],
    )


if __name__ == "__main__":
    main()
