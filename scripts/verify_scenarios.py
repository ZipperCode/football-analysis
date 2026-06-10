from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from football_analysis.db import StructuredRepository
from football_analysis.models import AgentFinding, Match, OddsSnapshot, RecommendationStatus
from football_analysis.scoring import score_match
from football_analysis.seed_data import build_seed_dataset
from football_analysis.service import AnalysisService
from football_analysis.settings import load_settings


def main() -> None:
    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'verify.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)
            service.ensure_seed_data()

            picks = service.picks_today()
            assert picks.analyses, "expected seeded match analyses"
            assert not picks.picks, "seed data must not enter real-money picks without live gate approval"
            assert any(
                analysis.recommendation.score_breakdown.get("live_gate", {}).get("passed") is False
                for analysis in picks.analyses
            ), "seed analyses must expose live gate rejection details"

            matches, _, findings = build_seed_dataset(settings.app.timezone)
            missing_odds = score_match(matches[0], [], findings, settings)
            assert missing_odds.status is RecommendationStatus.analysis_only, (
                "missing odds scenario must be analysis-only"
            )

            risky_payload = matches[0].model_dump()
            risky_payload["data_completeness"] = 0.40
            risky_match = Match(**risky_payload)
            risky = score_match(risky_match, [], [], settings)
            assert risky.status is RecommendationStatus.analysis_only, "low data without odds must not recommend"

            high_risk_odds = [
                OddsSnapshot(
                    id="risk-odds",
                    match_id=risky_match.id,
                    market_type="1x2",
                    source="fixture",
                    bookmaker="fixture",
                    outcome_odds={"HOME": 2.50},
                    market_average={"HOME": 2.00},
                    best_price={"HOME": 2.50},
                    movement=0.15,
                )
            ]
            high_risk_findings = [
                AgentFinding(
                    id="risk-finding",
                    match_id=risky_match.id,
                    agent_name="Risk Agent",
                    summary="conflicting source and volatile odds",
                    confidence=0.9,
                    risk_tags=["source_conflict", "odds_volatility", "low_data_quality"],
                )
            ]
            high_risk = score_match(risky_match, high_risk_odds, high_risk_findings, settings)
            assert high_risk.status is RecommendationStatus.rejected, "high risk scenario must be rejected"

            world_cup_payload = matches[0].model_dump()
            world_cup_payload.update(
                {
                    "id": "world-cup-provider-label",
                    "league": "International - FIFA World Cup",
                    "data_completeness": 0.80,
                }
            )
            world_cup_match = Match(**world_cup_payload)
            world_cup_odds = [
                OddsSnapshot(
                    id="world-cup-odds",
                    match_id=world_cup_match.id,
                    market_type="1x2",
                    source="fixture",
                    bookmaker="Bet365",
                    outcome_odds={"HOME": 2.40},
                    market_average={"HOME": 2.00},
                    best_price={"HOME": 2.40},
                )
            ]
            world_cup = score_match(world_cup_match, world_cup_odds, [], settings)
            assert world_cup.score_breakdown["league_profile"]["code"] == "WORLD_CUP", (
                "Odds-API.io World Cup provider label must match configured league profile"
            )

            brazil_payload = matches[0].model_dump()
            brazil_payload.update(
                {
                    "id": "single-bookmaker-brazil",
                    "league": "Brazil - Brasileiro Serie A",
                    "data_completeness": 0.56,
                }
            )
            brazil_match = Match(**brazil_payload)
            brazil_odds = [
                OddsSnapshot(
                    id="single-bookmaker-brazil-odds",
                    match_id=brazil_match.id,
                    market_type="asian_handicap",
                    line="-1",
                    source="odds_api_io",
                    bookmaker="Bet365",
                    outcome_odds={"HOME": 6.50},
                    market_average={"HOME": 4.80},
                    best_price={"HOME": 6.50},
                )
            ]
            brazil = score_match(brazil_match, brazil_odds, [], settings)
            assert brazil.score_breakdown["tier_policy"]["passed"] is False, (
                "tier policy payload must fail when bookmaker count is below league minimum"
            )
            assert "tier_min_bookmakers:1/2" in brazil.score_breakdown["tier_policy"]["gates_failed"], (
                "tier policy payload must report the bookmaker-count gate"
            )
        finally:
            repository.close()

    print("scenario verification passed")


if __name__ == "__main__":
    main()
