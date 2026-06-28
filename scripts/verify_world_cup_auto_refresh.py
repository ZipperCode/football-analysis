from __future__ import annotations

from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import BaseModel, Field

from football_analysis.db import StructuredRepository
from football_analysis.models import Match, OddsSnapshot
from football_analysis.service import AnalysisService
from football_analysis.settings import LeagueSettings, load_settings
from football_analysis.world_cup import recommend_world_cup, refresh_world_cup_data


class _FakeJob(BaseModel):
    status: str = "succeeded"
    summary: dict[str, Any] = Field(default_factory=dict)


class _FakeResult(BaseModel):
    job: _FakeJob = Field(default_factory=_FakeJob)
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)


class _FakeIngestion:
    def __init__(self, repository: StructuredRepository):
        self.repository = repository
        self.calls: list[tuple[str, str]] = []

    def ingest_fixtures(self, date: str, source: str = "api_football", league_code: str | None = None) -> _FakeResult:
        self.calls.append(("fixtures", source))
        match = Match(
            id=f"{source}:auto-refresh-match",
            league="International - FIFA World Cup",
            home_team="Home",
            away_team="Away",
            kickoff_at=datetime.fromisoformat(f"{date}T20:00:00+00:00"),
            data_completeness=0.9,
        )
        self.repository.upsert_model("matches", match.id, match)
        return _FakeResult(inserted=1, job=_FakeJob(summary={"matches": 1}))

    def ingest_odds(
        self,
        date: str | None = None,
        source: str = "api_football",
        league_code: str | None = None,
        max_events: int | None = None,
    ) -> _FakeResult:
        self.calls.append(("odds", source))
        match_id = "qqsd:auto-refresh-match" if source == "qqsd" else f"{source}:auto-refresh-match"
        snapshot = OddsSnapshot(
            id=f"{match_id}:{source}:1x2",
            match_id=match_id,
            market_type="1x2",
            source=source,
            bookmaker=f"{source} book",
            collected_at=datetime.now(timezone.utc),
            outcome_odds={"HOME": 1.9, "DRAW": 3.2, "AWAY": 4.0},
            market_average={"HOME": 1.9, "DRAW": 3.2, "AWAY": 4.0},
            best_price={"HOME": 1.9, "DRAW": 3.2, "AWAY": 4.0},
        )
        self.repository.upsert_model("odds", snapshot.id, snapshot)
        return _FakeResult(inserted=1, job=_FakeJob(summary={"odds_snapshots": 1}))

    def ingest_standings(self, league_code: str | None = None, source: str = "api_football") -> _FakeResult:
        self.calls.append(("standings", source))
        return _FakeResult(inserted=1, job=_FakeJob(summary={"payload_saved": True}))


def main() -> None:
    with TemporaryDirectory() as tmpdir:
        repository = StructuredRepository(f"sqlite:///{tmpdir}/verify.db")
        repository.initialize()
        try:
            settings = load_settings()
            settings.cache.enabled = True
            settings.leagues = [
                LeagueSettings(
                    code="WORLD_CUP",
                    name="FIFA World Cup",
                    country="World",
                    aliases=["International - FIFA World Cup"],
                    odds_api_slug="international-fifa-world-cup",
                    api_football_league_id=1,
                    tier="major_tournament",
                    analysis_depth="deep",
                    strategy_mode="live",
                    paper_only=False,
                )
            ]
            service = AnalysisService(settings, repository)
            fake_ingestion = _FakeIngestion(repository)
            service.ingestion = fake_ingestion

            report = refresh_world_cup_data(service, "2026-06-18", include_research=False)
            assert settings.cache.enabled is True
            assert [call for call in fake_ingestion.calls if call[0] == "odds"] == [
                ("odds", "qqsd"),
                ("odds", "odds_api_io"),
                ("odds", "the_odds_api"),
                ("odds", "api_football"),
            ]
            assert [item["operation"] for item in report["operations"]] == [
                "qqsd_fixtures",
                "qqsd_odds",
                "qqsd_standings",
                "odds_api_io_odds",
                "the_odds_api_odds",
                "api_football_odds",
            ]

            fake_ingestion.calls.clear()
            result = recommend_world_cup(
                service,
                "2026-06-18",
                include_parlays=True,
                refresh=True,
                refresh_research=False,
            )
            assert result["refresh"]["operations"][3]["operation"] == "odds_api_io_odds"
            assert any(call == ("odds", "the_odds_api") for call in fake_ingestion.calls)
        finally:
            repository.close()

    print("verify_world_cup_auto_refresh: ok")


if __name__ == "__main__":
    main()
