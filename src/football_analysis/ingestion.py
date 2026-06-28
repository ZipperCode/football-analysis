from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from football_analysis.contracts import HistoricalMatchRow
from football_analysis.datasources.api_football import APIFootballClient
from football_analysis.datasources.base import ClientContext, DataSourceError
from football_analysis.datasources.dongqiudi import DongqiudiClient
from football_analysis.datasources.football_data_org import FootballDataOrgClient
from football_analysis.datasources.football_data_uk import FootballDataUkClient
from football_analysis.datasources.leisu import LeisuClient
from football_analysis.datasources.odds_api_io import OddsApiIoClient
from football_analysis.datasources.qqsd import (
    QQSDClient,
    build_context_finding,
    map_archive_score_historical_rows,
    map_finished_match_asian_historical_row,
    find_league_entry,
    map_match_detail_match,
)
from football_analysis.datasources.sportmonks import SportmonksClient
from football_analysis.datasources.the_odds_api import TheOddsApiClient, sport_key_for_league
from football_analysis.db import StructuredRepository
from football_analysis.http_client import ProviderHttpClient
from football_analysis.models import AgentFinding, IngestionResult, JobRun, JobStatus, Match, MatchStatus, OddsSnapshot
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

            qqsd_client = QQSDClient(self._context(source)) if source == "qqsd" else None
            for match in matches:
                if qqsd_client is not None:
                    match = self._ingest_qqsd_match_context(qqsd_client, match, errors)
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
            elif source == "the_odds_api":
                client = TheOddsApiClient(self._context(source))
                sport_keys = self.settings.data_sources[source].sport_keys
                for league in self._leagues(league_code):
                    sport_key = sport_key_for_league(league.code, league.odds_api_slug, sport_keys)
                    if not sport_key:
                        errors.append(f"missing_sport_key:{league.code}")
                        continue
                    event_limit = max_events if max_events is not None else league.max_events
                    events, league_snapshots = client.odds(sport_key=sport_key)
                    selected_events = _limit_events(events, max_events=event_limit)
                    selected_match_ids = {event.id for event in selected_events}
                    for event in selected_events:
                        self.repository.upsert_model("matches", event.id, event)
                    snapshots.extend(
                        snapshot for snapshot in league_snapshots if snapshot.match_id in selected_match_ids
                    )
            elif source == "sportmonks":
                client = SportmonksClient(self._context(source))
                for league in self._leagues(league_code):
                    if not league.sportmonks_league_id:
                        errors.append(f"missing_sportmonks_league_id:{league.code}")
                        continue
                    event_limit = max_events if max_events is not None else league.max_events
                    events = _limit_events(
                        client.fixtures(
                            date=date or datetime.utcnow().date().isoformat(),
                            league_id=league.sportmonks_league_id,
                        ),
                        max_events=event_limit,
                    )
                    for event in events:
                        self.repository.upsert_model("matches", event.id, event)
                        fixture_id = event.external_ids.get("sportmonks_fixture")
                        if fixture_id:
                            snapshots.extend(client.odds_by_fixture(fixture_id))
            elif source == "qqsd":
                client = QQSDClient(self._context(source))
                for league in self._leagues(league_code):
                    league_matches = _filter_matches_by_league(
                        client.fixtures(date or datetime.utcnow().date().isoformat()),
                        league,
                    )
                    match_ids = {match.id for match in league_matches}
                    for event in _limit_events(league_matches, max_events=max_events or league.max_events):
                        if self.settings.ingestion.qqsd_live_context_enabled:
                            event = self._ingest_qqsd_match_context(client, event, errors)
                        existing = self.repository.get_model("matches", event.id, Match)
                        event = _preserve_richer_match_context(existing, event)
                        self.repository.upsert_model("matches", event.id, event)
                    snapshots.extend(
                        snapshot
                        for snapshot in client.odds(date=date, match_ids=match_ids)
                        if snapshot.match_id in match_ids
                    )
            elif source == "leisu":
                client = LeisuClient(self._context(source))
                for match_id in self._leisu_match_ids(league_code=league_code, max_events=max_events):
                    snapshots.extend(client.odds(match_id=match_id))

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
            elif source == "qqsd":
                client = QQSDClient(self._context(source))
                entry = find_league_entry(
                    client.league_list(),
                    identifiers=[
                        league.code,
                        league.name,
                        league.country or "",
                        *(league.aliases or []),
                    ],
                    season=season or league.season,
                )
                if entry is None:
                    raise DataSourceError(f"missing_qqsd_league_mapping:{league_code}")
                payload = client.league_standings(str(entry["MATCHID"]), str(entry["SEASONID"]))
            else:
                raise DataSourceError(f"unsupported_standings_source:{source}")
            self.repository.save_raw_payload(source, "standings:normalized", f"{source}:{league_code}:{season}", 200, payload, self.settings.cache.default_ttl_seconds)
            job = self._finish_job(job, JobStatus.succeeded, {"payload_saved": True})
            return IngestionResult(job=job, inserted=1)
        except Exception as exc:
            job = self._finish_job(job, JobStatus.failed, {}, error=f"{type(exc).__name__}: {exc}")
            return IngestionResult(job=job, errors=[job.error or "unknown error"])

    def ingest_historical_odds(
        self,
        league_code: str,
        snapshot_time: str,
        source: str = "the_odds_api",
        max_events: int | None = None,
    ) -> IngestionResult:
        job = self._start_job("ingest_historical_odds", source)
        errors: list[str] = []
        snapshots: list[OddsSnapshot] = []
        try:
            if source != "the_odds_api":
                raise DataSourceError(f"unsupported_historical_odds_source:{source}")
            client = TheOddsApiClient(self._context(source))
            sport_keys = self.settings.data_sources[source].sport_keys
            for league in self._leagues(league_code):
                sport_key = sport_key_for_league(league.code, league.odds_api_slug, sport_keys)
                if not sport_key:
                    errors.append(f"missing_sport_key:{league.code}")
                    continue
                event_limit = max_events if max_events is not None else league.max_events
                report = client.historical_odds(sport_key=sport_key, snapshot_time=snapshot_time)
                events = _limit_events(report.get("matches", []), max_events=event_limit)
                selected_match_ids = {event.id for event in events}
                for event in events:
                    self.repository.upsert_model("matches", event.id, event)
                snapshots.extend(
                    snapshot for snapshot in report.get("snapshots", []) if snapshot.match_id in selected_match_ids
                )
            snapshots = aggregate_market_prices(snapshots)
            for snapshot in snapshots:
                self.repository.upsert_model("odds", snapshot.id, snapshot)
            status = JobStatus.partial if errors else JobStatus.succeeded
            job = self._finish_job(
                job,
                status,
                {"odds_snapshots": len(snapshots), "snapshot_time": snapshot_time},
                error=";".join(errors) if errors else None,
            )
            return IngestionResult(job=job, inserted=len(snapshots), errors=errors)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            job = self._finish_job(
                job,
                JobStatus.failed,
                {"odds_snapshots": len(snapshots), "snapshot_time": snapshot_time},
                error=errors[-1],
            )
            return IngestionResult(job=job, inserted=len(snapshots), errors=errors)
    def ingest_intelligence(
        self,
        source: str = "dongqiudi",
        match_id: str | None = None,
        include_team_feeds: bool = True,
        article_detail_limit: int = 3,
        max_matches: int | None = None,
    ) -> IngestionResult:
        job = self._start_job("ingest_intelligence", source)
        errors: list[str] = []
        findings: list[AgentFinding] = []
        try:
            if source != "dongqiudi":
                raise DataSourceError(f"unsupported_intelligence_source:{source}")
            client = DongqiudiClient(self._context(source))
            matches = self._intelligence_matches(match_id=match_id, max_matches=max_matches)
            for match in matches:
                findings.extend(
                    client.intelligence_findings(
                        match,
                        include_team_feeds=include_team_feeds,
                        article_detail_limit=article_detail_limit,
                        errors=errors,
                    )
                )
            for finding in findings:
                self.repository.upsert_model("findings", finding.id, finding)
            status = JobStatus.partial if errors and findings else JobStatus.succeeded
            if errors and not findings:
                status = JobStatus.failed
            job = self._finish_job(job, status, {"findings": len(findings), "matches": len(matches)}, error="; ".join(errors) or None)
            return IngestionResult(job=job, inserted=len(findings), errors=errors)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            job = self._finish_job(job, JobStatus.failed, {"findings": len(findings)}, error=errors[-1])
            return IngestionResult(job=job, inserted=len(findings), errors=errors)


    def ingest_historical(
        self,
        league: str,
        season: str,
        path: str | None = None,
        download: bool = False,
        source: str = "football_data_uk",
        start_date: str | None = None,
        end_date: str | None = None,
        max_pages: int | None = None,
    ) -> IngestionResult:
        job = self._start_job("ingest_historical", source)
        rows: list[HistoricalMatchRow] = []
        try:
            if source == "football_data_uk":
                client = FootballDataUkClient(self._context("football_data_uk"))
                if path:
                    rows = client.parse_csv_file(league, season, Path(path))
                elif download:
                    rows = client.parse_csv_text(league, season, client.download_csv(league, season))
                else:
                    default_path = Path(self.settings.backtest.data_dir) / season / f"{league}.csv"
                    rows = client.parse_csv_file(league, season, default_path)
            elif source == "qqsd":
                if path or download:
                    raise DataSourceError("qqsd_historical_ignores_path_or_download")
                league_settings = self._league(league)
                client = QQSDClient(self._context("qqsd"))
                aliases = _league_alias_values(league_settings)
                start_day, end_day = _historical_date_window(
                    start_date=start_date,
                    end_date=end_date,
                    today=datetime.now(self.settings.app.tzinfo).date(),
                )
                day = start_day
                rows_by_id: dict[str, HistoricalMatchRow] = {}
                while day <= end_day:
                    for payload in client.archive_score_pages(day.isoformat(), max_pages=max_pages):
                        for row in map_archive_score_historical_rows(
                            payload,
                            league=league_settings.code,
                            season=season,
                            league_aliases=aliases,
                        ):
                            rows_by_id[row.id] = row
                    day += timedelta(days=1)
                rows = list(rows_by_id.values())
            elif source == "qqsd_local_asian":
                if path or download or start_date or end_date or max_pages:
                    raise DataSourceError("qqsd_local_asian_uses_local_finished_matches_only")
                league_settings = self._league(league)
                aliases = {value.lower() for value in _league_alias_values(league_settings)}
                all_odds = self.repository.list_models("odds", OddsSnapshot)
                rows_by_id: dict[str, HistoricalMatchRow] = {}
                for match in self.repository.list_models("matches", Match):
                    if match.league.strip().lower() not in aliases:
                        continue
                    row = map_finished_match_asian_historical_row(
                        match,
                        all_odds,
                        league=league_settings.code,
                        season=season,
                    )
                    if row is not None:
                        rows_by_id[row.id] = row
                rows = list(rows_by_id.values())
            else:
                raise DataSourceError(f"unsupported_historical_source:{source}")
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
        elif source == "sportmonks":
            client = SportmonksClient(self._context(source))
            for league in self._leagues(league_code):
                if league.sportmonks_league_id:
                    matches.extend(client.fixtures(date, league_id=league.sportmonks_league_id))
        elif source == "qqsd":
            client = QQSDClient(self._context(source))
            for league in self._leagues(league_code):
                matches.extend(_filter_matches_by_league(client.fixtures(date), league))
        elif source == "leisu":
            client = LeisuClient(self._context(source))
            matches.extend(client.fixtures(date=date))
        elif source == "dongqiudi":
            client = DongqiudiClient(self._context(source))
            if league_code:
                matches.extend(client.fixtures(date=date, league_code=league_code))
            else:
                matches.extend(client.fixtures(date=date))

        else:
            raise DataSourceError(f"unsupported_fixture_source:{source}")
        return matches

    def _ingest_qqsd_match_context(self, client: QQSDClient, match: Match, errors: list[str]) -> Match:
        fid = match.external_ids.get("qqsd_fid") or match.id.removeprefix("qqsd:")
        detail_payload = standings_payload = extreme_payload = None
        tools_payload = lingsi_payload = vote_payload = None
        injury_preview_payload = lineup_simple_payload = lineup_detail_payload = lineup_full_payload = None
        europe_odds_history_payload = odds_summary_payload = odds_heat_payload = None
        handicap_europe_payload = league_stats_payload = None
        betting_distribution_payload = same_odds_history_payload = odds_trend_payload = None
        bifa_trade_payload = company_list_payload = odds_change_list_payload = None
        odds_timeline_payload = None
        qqsd_context_errors: list[dict[str, str]] = []
        enriched = match
        try:
            detail_payload = client.match_detail(fid)
            self.repository.save_raw_payload(
                "qqsd",
                "match_detail",
                f"qqsd:40006:{fid}",
                200,
                detail_payload,
                self.settings.cache.fixtures_ttl_seconds,
            )
            enriched = map_match_detail_match(detail_payload, enriched)
        except Exception as exc:
            errors.append(f"qqsd_context_detail:{fid}:{type(exc).__name__}:{exc}")
            qqsd_context_errors.append({"key": "detail", "error": f"{type(exc).__name__}:{exc}"})
        try:
            standings_payload = client.standings(fid)
            self.repository.save_raw_payload(
                "qqsd",
                "standings",
                f"qqsd:41101:{fid}",
                200,
                standings_payload,
                self.settings.cache.default_ttl_seconds,
            )
        except Exception as exc:
            errors.append(f"qqsd_context_standings:{fid}:{type(exc).__name__}:{exc}")
            qqsd_context_errors.append({"key": "standings", "error": f"{type(exc).__name__}:{exc}"})
        try:
            extreme_payload = client.extreme_data()
            self.repository.save_raw_payload(
                "qqsd",
                "extreme_data",
                "qqsd:40034:global",
                200,
                extreme_payload,
                self.settings.cache.default_ttl_seconds,
            )
        except Exception as exc:
            errors.append(f"qqsd_context_extreme:{fid}:{type(exc).__name__}:{exc}")
            qqsd_context_errors.append({"key": "extreme_data", "error": f"{type(exc).__name__}:{exc}"})
        try:
            tools_payload = client.analysis_tools()
            self.repository.save_raw_payload(
                "qqsd",
                "analysis_tools",
                "qqsd:40046:global",
                200,
                tools_payload,
                self.settings.cache.default_ttl_seconds,
            )
        except Exception as exc:
            errors.append(f"qqsd_context_tools:{fid}:{type(exc).__name__}:{exc}")
            qqsd_context_errors.append({"key": "analysis_tools", "error": f"{type(exc).__name__}:{exc}"})
        try:
            lingsi_payload = client.lingsi(fid)
            self.repository.save_raw_payload(
                "qqsd",
                "lingsi",
                f"qqsd:home/lingsi:{fid}",
                200,
                lingsi_payload,
                self.settings.cache.default_ttl_seconds,
            )
        except Exception as exc:
            errors.append(f"qqsd_context_lingsi:{fid}:{type(exc).__name__}:{exc}")
            qqsd_context_errors.append({"key": "lingsi", "error": f"{type(exc).__name__}:{exc}"})
        try:
            vote_payload = client.vote_infos(fid)
            self.repository.save_raw_payload(
                "qqsd",
                "vote_infos",
                f"qqsd:team/voteinfos:{fid}",
                200,
                vote_payload,
                self.settings.cache.default_ttl_seconds,
            )
        except Exception as exc:
            errors.append(f"qqsd_context_vote_infos:{fid}:{type(exc).__name__}:{exc}")
            qqsd_context_errors.append({"key": "vote_infos", "error": f"{type(exc).__name__}:{exc}"})
        for key, endpoint, cache_key, loader in (
            ("injury_preview", "injury_preview", f"qqsd:40025:{fid}", lambda: client.injury_preview(fid)),
            ("lineup_simple", "lineup_simple", f"qqsd:41105:{fid}", lambda: client.lineup_simple(fid)),
            ("lineup_detail", "lineup_detail", f"qqsd:41106:{fid}", lambda: client.lineup_detail(fid)),
            ("lineup_full", "lineup_full", f"qqsd:41111:{fid}", lambda: client.lineup_full(fid)),
            ("company_list", "company_list", "qqsd:41108:global", client.company_list),
            ("odds_change_list", "odds_change_list", "qqsd:41112:global", client.odds_change_list),
        ):
            try:
                payload = loader()
                self.repository.save_raw_payload(
                    "qqsd",
                    endpoint,
                    cache_key,
                    200,
                    payload,
                    self.settings.cache.default_ttl_seconds,
                )
                if key == "injury_preview":
                    injury_preview_payload = payload
                elif key == "lineup_simple":
                    lineup_simple_payload = payload
                elif key == "lineup_detail":
                    lineup_detail_payload = payload
                elif key == "lineup_full":
                    lineup_full_payload = payload
                elif key == "company_list":
                    company_list_payload = payload
                elif key == "odds_change_list":
                    odds_change_list_payload = payload
            except Exception as exc:
                errors.append(f"qqsd_context_{key}:{fid}:{type(exc).__name__}:{exc}")
                qqsd_context_errors.append({"key": key, "error": f"{type(exc).__name__}:{exc}"})
        if self.settings.ingestion.qqsd_odds_timeline_enabled:
            try:
                odds_timeline_payload = client.match_odds_timeline_bundle(
                    fid,
                    vsdate=match.kickoff_at.astimezone(self.settings.app.tzinfo).strftime("%Y-%m-%d %H:%M:%S"),
                    company_name=self.settings.ingestion.qqsd_timeline_company_name,
                )
                self.repository.save_raw_payload(
                    "qqsd",
                    "odds_timeline",
                    f"qqsd:odds_timeline:{fid}",
                    200,
                    odds_timeline_payload,
                    self.settings.cache.default_ttl_seconds,
                )
            except Exception as exc:
                errors.append(f"qqsd_context_odds_timeline:{fid}:{type(exc).__name__}:{exc}")
                qqsd_context_errors.append({"key": "odds_timeline", "error": f"{type(exc).__name__}:{exc}"})
        for key, endpoint, loader in (
            ("europe_odds_history", "europe_odds_history", lambda: client.europe_odds_history(fid)),
            ("odds_summary", "odds_summary", lambda: client.odds_summary(fid)),
            ("odds_heat", "odds_heat", lambda: client.odds_heat(fid)),
            ("handicap_europe_odds", "handicap_europe_odds", lambda: client.handicap_europe_odds(fid)),
            ("league_stats", "league_stats", lambda: client.league_stats(fid)),
            ("betting_distribution", "betting_distribution", lambda: client.betting_distribution(fid)),
            ("same_odds_history", "same_odds_history", lambda: client.same_odds_history(fid)),
            ("odds_trend", "odds_trend", lambda: client.odds_trend(fid)),
            ("bifa_trade", "bifa_trade", lambda: client.bifa_trade(fid)),
        ):
            try:
                payload = loader()
                self.repository.save_raw_payload(
                    "qqsd",
                    endpoint,
                    f"qqsd:{endpoint}:{fid}",
                    200,
                    payload,
                    self.settings.cache.default_ttl_seconds,
                )
                if key == "europe_odds_history":
                    europe_odds_history_payload = payload
                elif key == "odds_summary":
                    odds_summary_payload = payload
                elif key == "odds_heat":
                    odds_heat_payload = payload
                elif key == "handicap_europe_odds":
                    handicap_europe_payload = payload
                elif key == "league_stats":
                    league_stats_payload = payload
                elif key == "betting_distribution":
                    betting_distribution_payload = payload
                elif key == "same_odds_history":
                    same_odds_history_payload = payload
                elif key == "odds_trend":
                    odds_trend_payload = payload
                elif key == "bifa_trade":
                    bifa_trade_payload = payload
            except Exception as exc:
                errors.append(f"qqsd_context_{key}:{fid}:{type(exc).__name__}:{exc}")
                qqsd_context_errors.append({"key": key, "error": f"{type(exc).__name__}:{exc}"})
        finding = build_context_finding(
            enriched,
            detail_payload=detail_payload,
            standings_payload=standings_payload,
            extreme_payload=extreme_payload,
            tools_payload=tools_payload,
            injury_preview_payload=injury_preview_payload,
            lineup_simple_payload=lineup_simple_payload,
            lineup_detail_payload=lineup_detail_payload,
            lineup_full_payload=lineup_full_payload,
            lingsi_payload=lingsi_payload,
            vote_payload=vote_payload,
            europe_odds_history_payload=europe_odds_history_payload,
            odds_summary_payload=odds_summary_payload,
            odds_heat_payload=odds_heat_payload,
            handicap_europe_payload=handicap_europe_payload,
            league_stats_payload=league_stats_payload,
            betting_distribution_payload=betting_distribution_payload,
            same_odds_history_payload=same_odds_history_payload,
            odds_trend_payload=odds_trend_payload,
            bifa_trade_payload=bifa_trade_payload,
            company_list_payload=company_list_payload,
            odds_change_list_payload=odds_change_list_payload,
            odds_timeline_payload=odds_timeline_payload,
            errors=qqsd_context_errors,
        )
        if finding is not None:
            self.repository.upsert_model("findings", finding.id, finding)
        return enriched
    def _leisu_match_ids(self, league_code: str | None, max_events: int | None) -> list[str]:
        league_names = {league.name for league in self._leagues(league_code)} if league_code else None
        match_ids: list[str] = []
        for match in self.repository.list_models("matches", Match):
            if league_names is not None and match.league not in league_names:
                continue
            leisu_match_id = match.external_ids.get("leisu_match")
            if leisu_match_id:
                match_ids.append(leisu_match_id)
            if max_events is not None and len(match_ids) >= max_events:
                break
        return match_ids

    def _intelligence_matches(self, match_id: str | None, max_matches: int | None) -> list[Match]:
        if match_id:
            match = self.repository.get_model("matches", match_id, Match)
            if match is None:
                raise DataSourceError(f"unknown_match:{match_id}")
            return [match]
        matches = [
            match
            for match in self.repository.list_models("matches", Match)
            if match.external_ids.get("dongqiudi_match")
        ]
        return matches[:max_matches] if max_matches is not None else matches


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




def _league_alias_values(league: LeagueSettings) -> set[str]:
    values = {
        league.code,
        league.name,
        league.country,
        league.football_data_org_code,
        league.football_data_uk_code,
        *(league.aliases or []),
    }
    if league.code == "FIN_VEIKKAUSLIIGA":
        values.update({"芬超", "芬兰超", "芬兰超级联赛"})
    return {str(value).strip() for value in values if value and str(value).strip()}


def _historical_date_window(
    *,
    start_date: str | None,
    end_date: str | None,
    today: date,
) -> tuple[date, date]:
    end_day = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else today
    start_day = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else end_day - timedelta(days=29)
    if start_day > end_day:
        raise DataSourceError("invalid_historical_date_window:start_after_end")
    return start_day, end_day

def _filter_matches_by_league(matches: list[Match], league: LeagueSettings) -> list[Match]:
    normalized = {value.lower() for value in _league_alias_values(league)}
    return [match for match in matches if match.league.strip().lower() in normalized]


def _preserve_richer_match_context(existing: Match | None, incoming: Match) -> Match:
    if existing is None or incoming.data_completeness >= existing.data_completeness:
        return incoming
    external_ids = dict(incoming.external_ids)
    external_ids.update(existing.external_ids)
    return existing.model_copy(
        update={
            "kickoff_at": incoming.kickoff_at,
            "status": incoming.status,
            "home_score": incoming.home_score if incoming.home_score is not None else existing.home_score,
            "away_score": incoming.away_score if incoming.away_score is not None else existing.away_score,
            "external_ids": external_ids,
        }
    )


def _chunks(items: list[str], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]
