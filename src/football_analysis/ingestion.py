from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from football_analysis.contracts import HistoricalMatchRow
from football_analysis.datasources.api_football import APIFootballClient
from football_analysis.datasources.base import ClientContext, DataSourceError
from football_analysis.datasources.football_data_org import FootballDataOrgClient
from football_analysis.datasources.football_data_uk import FootballDataUkClient
from football_analysis.datasources.odds_api_io import OddsApiIoClient
from football_analysis.db import StructuredRepository
from football_analysis.http_client import ProviderHttpClient
from football_analysis.models import IngestionResult, JobRun, JobStatus, Match, MatchStatus, OddsSnapshot
from football_analysis.settings import LeagueSettings, Settings


class IngestionService:
    def __init__(self, settings: Settings, repository: StructuredRepository):
        self.settings = settings
        self.repository = repository
        self.http = ProviderHttpClient(settings, repository)

    def ingest_fixtures(self, date: str, source: str = "api_football", league_code: str | None = None) -> IngestionResult:
        job = self._start_job("ingest_fixtures", source)
        errors: list[str] = []
        matches: list[Match] = []
        try:
            matches = self._collect_fixtures(date=date, source=source, league_code=league_code)

            for match in matches:
                self.repository.upsert_model("matches", match.id, match)
            job = self._finish_job(job, JobStatus.succeeded, {"matches": len(matches)})
            return IngestionResult(job=job, inserted=len(matches), updated=0, errors=errors)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            job = self._finish_job(job, JobStatus.failed, {"matches": len(matches)}, error=errors[-1])
            return IngestionResult(job=job, inserted=len(matches), errors=errors)

    def ingest_results(self, date: str, source: str = "api_football", league_code: str | None = None) -> IngestionResult:
        job = self._start_job("ingest_results", source)
        errors: list[str] = []
        matches: list[Match] = []
        try:
            matches = self._collect_fixtures(date=date, source=source, league_code=league_code)
            for match in matches:
                self.repository.upsert_model("matches", match.id, match)
            finished_matches = sum(1 for match in matches if match.status is MatchStatus.finished)
            job = self._finish_job(
                job,
                JobStatus.succeeded,
                {"matches": len(matches), "finished_matches": finished_matches},
            )
            return IngestionResult(job=job, inserted=len(matches), updated=finished_matches, errors=errors)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            job = self._finish_job(job, JobStatus.failed, {"matches": len(matches)}, error=errors[-1])
            return IngestionResult(job=job, inserted=len(matches), errors=errors)

    def ingest_odds(
        self,
        date: str | None = None,
        source: str = "api_football",
        league_code: str | None = None,
        max_events: int | None = None,
    ) -> IngestionResult:
        job = self._start_job("ingest_odds", source)
        errors: list[str] = []
        snapshots: list[OddsSnapshot] = []
        try:
            if source == "api_football":
                client = APIFootballClient(self._context(source))
                for league in self._leagues(league_code):
                    if league.api_football_league_id and league.season:
                        snapshots.extend(client.odds(date=date, league=league.api_football_league_id, season=league.season))
            elif source == "odds_api_io":
                client = OddsApiIoClient(self._context(source))
                for league in self._leagues(league_code):
                    if not league.odds_api_slug:
                        continue
                    # A CLI/API override wins; otherwise each league controls its own quota footprint.
                    event_limit = max_events if max_events is not None else league.max_events
                    events = _limit_events(client.events(league=league.odds_api_slug), max_events=event_limit)
                    event_ids: list[str] = []
                    for event in events:
                        self.repository.upsert_model("matches", event.id, event)
                        event_id = event.external_ids.get("odds_api_io_event")
                        if event_id:
                            event_ids.append(event_id)
                    for batch in _chunks(event_ids, 10):
                        snapshots.extend(client.odds_multi(event_ids=batch))
            else:
                raise DataSourceError(f"unsupported_odds_source:{source}")

            snapshots = aggregate_market_prices(snapshots)
            for snapshot in snapshots:
                self.repository.upsert_model("odds", snapshot.id, snapshot)
            job = self._finish_job(job, JobStatus.succeeded, {"odds_snapshots": len(snapshots)})
            return IngestionResult(job=job, inserted=len(snapshots), errors=errors)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            job = self._finish_job(job, JobStatus.failed, {"odds_snapshots": len(snapshots)}, error=errors[-1])
            return IngestionResult(job=job, inserted=len(snapshots), errors=errors)

    def ingest_standings(self, league_code: str, season: int | None = None, source: str = "api_football") -> IngestionResult:
        job = self._start_job("ingest_standings", source)
        try:
            league = self._league(league_code)
            if source == "api_football":
                if not league.api_football_league_id:
                    raise DataSourceError(f"missing_api_football_league_id:{league_code}")
                payload = APIFootballClient(self._context(source)).standings(
                    league.api_football_league_id,
                    season or league.season or datetime.utcnow().year,
                )
            elif source == "football_data_org":
                if not league.football_data_org_code:
                    raise DataSourceError(f"missing_football_data_org_code:{league_code}")
                payload = FootballDataOrgClient(self._context(source)).standings(league.football_data_org_code)
            else:
                raise DataSourceError(f"unsupported_standings_source:{source}")
            self.repository.save_raw_payload(source, "standings:normalized", f"{source}:{league_code}:{season}", 200, payload, self.settings.cache.default_ttl_seconds)
            job = self._finish_job(job, JobStatus.succeeded, {"payload_saved": True})
            return IngestionResult(job=job, inserted=1)
        except Exception as exc:
            job = self._finish_job(job, JobStatus.failed, {}, error=f"{type(exc).__name__}: {exc}")
            return IngestionResult(job=job, errors=[job.error or "unknown error"])

    def ingest_historical(
        self,
        league: str,
        season: str,
        path: str | None = None,
        download: bool = False,
    ) -> IngestionResult:
        job = self._start_job("ingest_historical", "football_data_uk")
        rows: list[HistoricalMatchRow] = []
        try:
            client = FootballDataUkClient(self._context("football_data_uk"))
            if path:
                rows = client.parse_csv_file(league, season, Path(path))
            elif download:
                rows = client.parse_csv_text(league, season, client.download_csv(league, season))
            else:
                default_path = Path(self.settings.backtest.data_dir) / season / f"{league}.csv"
                rows = client.parse_csv_file(league, season, default_path)
            for row in rows:
                self.repository.upsert_model("historical_matches", row.id, row)
            job = self._finish_job(job, JobStatus.succeeded, {"historical_matches": len(rows)})
            return IngestionResult(job=job, inserted=len(rows))
        except Exception as exc:
            job = self._finish_job(job, JobStatus.failed, {"historical_matches": len(rows)}, error=f"{type(exc).__name__}: {exc}")
            return IngestionResult(job=job, inserted=len(rows), errors=[job.error or "unknown error"])

    def _context(self, source_id: str) -> ClientContext:
        source = self.settings.data_sources[source_id]
        return ClientContext(source_id, source, self.settings, self.repository, self.http)

    def _leagues(self, league_code: str | None) -> list[LeagueSettings]:
        if league_code:
            return [self._league(league_code)]
        return self.settings.leagues

    def _league(self, league_code: str) -> LeagueSettings:
        for league in self.settings.leagues:
            if league.code.lower() == league_code.lower():
                return league
        raise DataSourceError(f"unknown_league:{league_code}")

    def _collect_fixtures(self, date: str, source: str, league_code: str | None) -> list[Match]:
        matches: list[Match] = []
        if source == "api_football":
            client = APIFootballClient(self._context(source))
            for league in self._leagues(league_code):
                if league.api_football_league_id and league.season:
                    matches.extend(client.fixtures(date, league.api_football_league_id, league.season))
        elif source == "football_data_org":
            client = FootballDataOrgClient(self._context(source))
            for league in self._leagues(league_code):
                if league.football_data_org_code:
                    matches.extend(client.matches(date, date, league.football_data_org_code))
        elif source == "odds_api_io":
            client = OddsApiIoClient(self._context(source))
            for league in self._leagues(league_code):
                if league.odds_api_slug:
                    matches.extend(client.events(league=league.odds_api_slug))
        else:
            raise DataSourceError(f"unsupported_fixture_source:{source}")
        return matches

    def _start_job(self, job_type: str, source: str | None) -> JobRun:
        job = JobRun(id=str(uuid4()), job_type=job_type, status=JobStatus.started, source=source)
        self.repository.upsert_model("jobs", job.id, job)
        return job

    def _finish_job(self, job: JobRun, status: JobStatus, summary: dict, error: str | None = None) -> JobRun:
        finished = JobRun(
            id=job.id,
            job_type=job.job_type,
            status=status,
            source=job.source,
            started_at=job.started_at,
            finished_at=datetime.utcnow(),
            summary=summary,
            error=error,
        )
        self.repository.upsert_model("jobs", finished.id, finished)
        return finished


def aggregate_market_prices(snapshots: list[OddsSnapshot]) -> list[OddsSnapshot]:
    groups: dict[tuple[str, str, str | None], list[OddsSnapshot]] = {}
    for snapshot in snapshots:
        groups.setdefault((snapshot.match_id, snapshot.market_type.value, snapshot.line), []).append(snapshot)

    aggregated: list[OddsSnapshot] = []
    for group in groups.values():
        selections = sorted({selection for snapshot in group for selection in snapshot.outcome_odds})
        averages: dict[str, float] = {}
        best: dict[str, float] = {}
        for selection in selections:
            prices = [snapshot.outcome_odds[selection] for snapshot in group if selection in snapshot.outcome_odds]
            if prices:
                averages[selection] = round(sum(prices) / len(prices), 4)
                best[selection] = max(prices)
        for snapshot in group:
            aggregated.append(snapshot.model_copy(update={"market_average": averages, "best_price": best}))
    return aggregated


def _limit_events(events: list[Match], max_events: int | None) -> list[Match]:
    ordered = sorted(events, key=lambda item: item.kickoff_at)
    if max_events is None or max_events <= 0:
        return ordered
    return ordered[:max_events]


def _chunks(items: list[str], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]
