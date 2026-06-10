from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from football_analysis.db import StructuredRepository
from football_analysis.models import IngestionResult, JobRun, JobStatus
from football_analysis.service import AnalysisService
from football_analysis.settings import load_settings


class FakeIngestion:
    def __init__(
        self,
        fixture_inserted: int = 1,
        odds_inserted: int = 1,
        odds_inserted_by_source: dict[str, int] | None = None,
    ) -> None:
        self.calls: list[tuple] = []
        self.fixture_inserted = fixture_inserted
        self.odds_inserted = odds_inserted
        self.odds_inserted_by_source = odds_inserted_by_source or {}

    def ingest_fixtures(self, date: str, source: str, league_code: str | None = None) -> IngestionResult:
        self.calls.append(("fixtures", source, league_code, date))
        return _result("fixtures", source, league_code, inserted=self.fixture_inserted)

    def ingest_odds(
        self,
        date: str | None = None,
        source: str = "odds_api_io",
        league_code: str | None = None,
        max_events: int | None = None,
    ) -> IngestionResult:
        self.calls.append(("odds", source, league_code, date, max_events))
        inserted = self.odds_inserted_by_source.get(source, self.odds_inserted)
        return _result("odds", source, league_code, inserted=inserted)


def main() -> None:
    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'live-refresh.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)
            fake_ingestion = FakeIngestion()
            service.ingestion = fake_ingestion  # type: ignore[assignment]

            from football_analysis.live_refresh import run_live_refresh

            report = run_live_refresh(
                service,
                date="2026-01-18",
                fixture_source="api_football",
                odds_source="odds_api_io",
                max_events=3,
                include_past=True,
            )
            assert report.dry_run is False
            assert report.leagues == ["EPL", "SERIE_A"], "live refresh should target active profile leagues only"
            assert fake_ingestion.calls == [
                ("fixtures", "api_football", "EPL", "2026-01-18"),
                ("fixtures", "api_football", "SERIE_A", "2026-01-18"),
                ("odds", "odds_api_io", "EPL", None, 3),
                ("odds", "odds_api_io", "SERIE_A", None, 3),
            ], "live refresh must run fixtures before odds for active profile leagues"
            assert len(report.operations) == 4
            assert all(operation.executed for operation in report.operations)
            assert len(report.fixture_results) == 2
            assert len(report.odds_results) == 2
            assert report.preflight.ready_to_bet is False
            assert any(issue.startswith("preflight:") for issue in report.issues)
        finally:
            repository.close()

    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'live-refresh-empty.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)
            service.ingestion = FakeIngestion(fixture_inserted=0, odds_inserted=0)  # type: ignore[assignment]

            from football_analysis.live_refresh import run_live_refresh

            empty = run_live_refresh(service, date="2026-01-18", league="SERIE_A", include_past=True)
            assert "fixtures_refresh_empty:SERIE_A" in empty.issues, "empty fixture refresh should be explicit"
            assert "odds_refresh_empty:SERIE_A" in empty.issues, "empty odds refresh should be explicit"

            active_empty = run_live_refresh(service, date="2026-01-18", include_past=True)
            assert "active_profile_refresh_empty:EPL,SERIE_A" in active_empty.issues, (
                "empty active-profile refresh should be reported as a profile calendar/data gap"
            )
            assert "consider_scope_live_leagues" in active_empty.issues, (
                "operator should be told to scan live leagues when active profiles have no markets"
            )
        finally:
            repository.close()

    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'live-refresh-fallback.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)
            fake_ingestion = FakeIngestion(
                fixture_inserted=1,
                odds_inserted_by_source={"odds_api_io": 0, "api_football": 1},
            )
            service.ingestion = fake_ingestion  # type: ignore[assignment]

            from football_analysis.live_refresh import run_live_refresh

            fallback = run_live_refresh(service, date="2026-01-18", league="LALIGA", include_past=True)
            assert ("odds", "odds_api_io", "LALIGA", None, None) in fake_ingestion.calls
            assert ("odds", "api_football", "LALIGA", "2026-01-18", None) in fake_ingestion.calls
            assert "odds_source_fallback:LALIGA:odds_api_io->api_football" in fallback.issues
            assert len([operation for operation in fallback.operations if operation.kind == "odds"]) == 2

            fixed_source = FakeIngestion(
                fixture_inserted=1,
                odds_inserted_by_source={"odds_api_io": 0, "api_football": 1},
            )
            service.ingestion = fixed_source  # type: ignore[assignment]
            strict = run_live_refresh(
                service,
                date="2026-01-18",
                league="LALIGA",
                odds_source="odds_api_io",
                include_past=True,
            )
            assert ("odds", "api_football", "LALIGA", "2026-01-18", None) not in fixed_source.calls
            assert "odds_source_fallback:LALIGA:odds_api_io->api_football" not in strict.issues
        finally:
            repository.close()

    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'live-refresh-dry.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)
            fake_ingestion = FakeIngestion()
            service.ingestion = fake_ingestion  # type: ignore[assignment]

            from football_analysis.live_refresh import run_live_refresh

            dry = run_live_refresh(service, date="2026-01-18", dry_run=True, include_past=True)
            assert dry.dry_run is True
            assert fake_ingestion.calls == [], "dry-run must not spend remote quota"
            assert dry.leagues == ["EPL", "SERIE_A"]
            assert dry.scope == "active-profiles"
            assert dry.refresh_requirements, "dry-run must expose active-profile odds refresh requirements"
            target_keys = {
                (item.refresh_league_code, item.market_type, tuple(item.selections))
                for item in dry.refresh_requirements
            }
            assert ("EPL", "1x2", ("HOME",)) in target_keys, (
                "dry-run must identify the E0 home-value odds gap"
            )
            assert ("SERIE_A", "asian_handicap", ("AH_AWAY",)) in target_keys, (
                "dry-run must identify the I1 AH-away odds gap"
            )
            assert all(not operation.executed for operation in dry.operations)
            assert dry.fixture_results == []
            assert dry.odds_results == []

            fixed_live_leagues = run_live_refresh(
                service,
                date="2026-01-18",
                fixture_source="api_football",
                odds_source="odds_api_io",
                scope="live-leagues",
                dry_run=True,
                include_past=True,
            )
            assert fake_ingestion.calls == [], "live-leagues dry-run must not spend remote quota"
            assert fixed_live_leagues.scope == "live-leagues"
            assert fixed_live_leagues.leagues == [
                "ARG_PRIMERA",
                "A_LEAGUE",
                "BRA_SERIE_A",
                "EPL",
                "J1",
                "K_LEAGUE_1",
                "LALIGA",
                "LIGA_MX",
                "MLS",
                "SERIE_A",
            ], "live-leagues scope should cover every non-paper live league"
            assert "odds_source_unmapped:ARG_PRIMERA:odds_api_io" in fixed_live_leagues.issues
            assert "odds_source_unmapped:J1:odds_api_io" in fixed_live_leagues.issues
            assert "odds_source_unmapped:LIGA_MX:odds_api_io" in fixed_live_leagues.issues

            auto = run_live_refresh(
                service,
                date="2026-01-18",
                scope="live-leagues",
                dry_run=True,
                include_past=True,
            )
            operations = {(operation.kind, operation.league_code): operation for operation in auto.operations}
            assert operations[("fixtures", "BRA_SERIE_A")].source == "odds_api_io", (
                "auto fixtures should use Odds-API.io events when API-FOOTBALL mapping is unavailable"
            )
            assert operations[("odds", "J1")].source == "api_football", (
                "auto odds should fall back to API-FOOTBALL when Odds-API.io slug is unavailable"
            )
            assert operations[("odds", "ARG_PRIMERA")].source == "api_football"
            assert operations[("odds", "LIGA_MX")].source == "api_football"
            assert not any("source_unmapped" in issue for issue in auto.issues), (
                "auto source selection should avoid unmapped live-league refresh operations"
            )
        finally:
            repository.close()

    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'live-refresh-fallback-guard.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)
            guarded_ingestion = FakeIngestion(
                fixture_inserted=1,
                odds_inserted_by_source={"odds_api_io": 0, "api_football": 1},
            )
            service.ingestion = guarded_ingestion  # type: ignore[assignment]

            from football_analysis.live_refresh import run_live_refresh

            guarded = run_live_refresh(
                service,
                date="2026-01-18",
                scope="live-leagues",
                max_events=1,
                include_past=True,
            )
            assert ("odds", "api_football", "LALIGA", "2026-01-18", 1) not in guarded_ingestion.calls, (
                "live-leagues scan should not spend fallback odds quota by default"
            )
            assert "odds_fallback_skipped:LALIGA:api_football" in guarded.issues

            allowed_ingestion = FakeIngestion(
                fixture_inserted=1,
                odds_inserted_by_source={"odds_api_io": 0, "api_football": 1},
            )
            service.ingestion = allowed_ingestion  # type: ignore[assignment]
            allowed = run_live_refresh(
                service,
                date="2026-01-18",
                scope="live-leagues",
                max_events=1,
                include_past=True,
                allow_odds_fallback=True,
            )
            assert ("odds", "api_football", "LALIGA", "2026-01-18", 1) in allowed_ingestion.calls
            assert "odds_source_fallback:LALIGA:odds_api_io->api_football" in allowed.issues
        finally:
            repository.close()

    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'live-refresh-unmapped.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)
            fake_ingestion = FakeIngestion()
            service.ingestion = fake_ingestion  # type: ignore[assignment]

            from football_analysis.live_refresh import run_live_refresh

            unmapped = run_live_refresh(
                service,
                date="2026-01-18",
                league="J1",
                odds_source="odds_api_io",
                include_past=True,
            )
            assert "odds_source_unmapped:J1:odds_api_io" in unmapped.issues
            assert fake_ingestion.calls == [
                ("fixtures", "api_football", "J1", "2026-01-18"),
            ], "unmapped odds source should be skipped instead of reported as an executed empty refresh"
            odds_operations = [operation for operation in unmapped.operations if operation.kind == "odds"]
            assert odds_operations and odds_operations[0].executed is False
        finally:
            repository.close()

    with TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'live-refresh-cli.db'}"
        repository = StructuredRepository(env["DATABASE_URL"])
        repository.initialize()
        repository.close()
        completed = subprocess.run(
            [
                "footballctl",
                "live-refresh",
                "--date",
                "2026-01-18",
                "--scope",
                "live-leagues",
                "--dry-run",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        payload = json.loads(completed.stdout)
        assert payload["dry_run"] is True
        assert payload["scope"] == "live-leagues"
        assert payload["fixture_source"] == "auto"
        assert payload["odds_source"] == "auto"
        assert payload["allow_odds_fallback"] is False
        assert "BRA_SERIE_A" in payload["leagues"]
        assert not any("source_unmapped" in issue for issue in payload["issues"])
        assert payload["fixture_results"] == []
        assert payload["odds_results"] == []
        assert payload["preflight"]["ready_to_bet"] is False
        assert "refresh_requirements" in payload, "CLI live-refresh JSON should expose targeted odds gaps"

        fallback_completed = subprocess.run(
            [
                "footballctl",
                "live-refresh",
                "--date",
                "2026-01-18",
                "--scope",
                "live-leagues",
                "--allow-odds-fallback",
                "--dry-run",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        fallback_payload = json.loads(fallback_completed.stdout)
        assert fallback_payload["allow_odds_fallback"] is True

        os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'live-refresh-api.db'}"
        from fastapi.testclient import TestClient
        from football_analysis.api import app

        client = TestClient(app)
        response = client.post(
            "/live/refresh",
            params={
                "date": "2026-01-18",
                "scope": "live-leagues",
                "dry_run": True,
                "allow_odds_fallback": True,
            },
        )
        assert response.status_code == 200
        assert response.json()["allow_odds_fallback"] is True

    print("live refresh verification passed")


def _result(kind: str, source: str, league_code: str | None, inserted: int) -> IngestionResult:
    return IngestionResult(
        job=JobRun(
            id=f"{kind}-{source}-{league_code}",
            job_type=f"ingest_{kind}",
            status=JobStatus.succeeded,
            source=source,
            summary={"league_code": league_code},
        ),
        inserted=inserted,
    )


if __name__ == "__main__":
    main()
