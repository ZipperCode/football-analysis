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
            assert any(pick.status is RecommendationStatus.recommended for pick in picks.picks), (
                "complete data scenario should produce at least one recommendation"
            )

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
        finally:
            repository.close()

    print("scenario verification passed")


if __name__ == "__main__":
    main()
