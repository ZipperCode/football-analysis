from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from football_analysis.contracts import SourceResponse
from football_analysis.datasources.base import ClientContext
from football_analysis.datasources.football_data_uk import parse_csv_text
from football_analysis.datasources.odds_api_io import OddsApiIoClient, map_events
from football_analysis.datasources.sportmonks import (
    SportmonksClient,
    map_fixtures_payload as map_sportmonks_fixtures_payload,
    map_pre_match_odds_payload as map_sportmonks_pre_match_odds_payload,
)
from football_analysis.datasources.the_odds_api import (
    TheOddsApiClient,
    map_historical_odds_payload,
    map_odds_payload,
    map_sports_payload,
    sport_key_for_league,
)
from football_analysis.db import StructuredRepository
from football_analysis.models import Match
from football_analysis.service import AnalysisService
from football_analysis.settings import load_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote", action="store_true", help="Enable remote source probes.")
    parser.add_argument("--no-remote", action="store_true", help="Keep remote probes disabled.")
    args = parser.parse_args()

    if args.remote and args.no_remote:
        raise SystemExit("Use only one of --remote or --no-remote.")

    previous = os.getenv("FOOTBALL_VALIDATE_REMOTE")
    os.environ["FOOTBALL_VALIDATE_REMOTE"] = "1" if args.remote else "0"
    try:
        with TemporaryDirectory() as tmp:
            settings = load_settings()
            settings.storage.database_url = f"sqlite:///{Path(tmp) / 'sources.db'}"
            repository = StructuredRepository(settings.storage.database_url)
            repository.initialize()
            try:
                service = AnalysisService(settings, repository)
                health = asyncio.run(service.sources_health())
                assert health, "expected configured data sources"
                for item in health:
                    assert item.source_id, "source id missing"
                    assert item.name, "source name missing"
                    assert "<redacted>" not in item.detail, "health detail should not contain redacted markers"
                    if item.credential_present:
                        assert item.state.value != "missing_credentials"

                legacy_ah_csv = "\n".join(
                    [
                        "Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A,MaxH,MaxD,MaxA,AvgH,AvgD,AvgA,BbAH,BbAHh,BbMxAHH,BbAvAHH,BbMxAHA,BbAvAHA",
                        "08/08/15,Lazio,Bologna,2,1,1.75,3.60,5.25,1.80,3.70,5.50,1.74,3.55,5.10,18,-0.5,1.92,1.88,2.05,1.98",
                    ]
                )
                legacy_rows = parse_csv_text("I1", "1516", legacy_ah_csv)
                assert len(legacy_rows) == 1, "legacy AH fixture row should parse"
                legacy = legacy_rows[0]
                assert legacy.ah_line == -0.5, "legacy BbAHh must map to AH line"
                assert legacy.ah_home_odds == 1.92, "legacy BbMxAHH must map to home AH odds"
                assert legacy.ah_away_odds == 2.05, "legacy BbMxAHA must map to away AH odds"
                assert legacy.avg_ah_home_odds == 1.88, "legacy BbAvAHH must map to average home AH odds"
                assert legacy.avg_ah_away_odds == 1.98, "legacy BbAvAHA must map to average away AH odds"

                extra_csv = "\n".join(
                    [
                        "Country,League,Season,Date,Time,Home,Away,HG,AG,Res,PSCH,PSCD,PSCA,MaxCH,MaxCD,MaxCA,AvgCH,AvgCD,AvgCA",
                        "Brazil,Serie A,2012,19/05/2012,22:30,Palmeiras,Portuguesa,1,1,D,1.75,3.86,5.25,1.76,3.87,5.31,1.69,3.5,4.9",
                    ]
                )
                extra_rows = parse_csv_text("BRA", "2526", extra_csv)
                assert len(extra_rows) == 1, "extra league CSV row should parse"
                assert extra_rows[0].season == "2012", "extra league CSV must preserve its Season column"
                assert extra_rows[0].home_team == "Palmeiras"
                assert extra_rows[0].home_goals == 1 and extra_rows[0].away_goals == 1
                assert extra_rows[0].closing_home_odds == 1.75
                assert extra_rows[0].avg_home_odds == 1.69

                from football_analysis.datasources.football_data_uk import FootballDataUkClient

                extra_csv_http = FakeHttp(payloads={"/new/BRA.csv": legacy_ah_csv})
                football_data_uk = service.ingestion._context("football_data_uk")
                FootballDataUkClient(
                    ClientContext(
                        football_data_uk.provider,
                        football_data_uk.source,
                        football_data_uk.settings,
                        football_data_uk.repository,
                        extra_csv_http,
                    )
                ).download_csv("BRA", "2526")
                assert extra_csv_http.calls[-1]["endpoint"] == "/new/BRA.csv", (
                    "football-data.co.uk extra leagues must use /new/{code}.csv"
                )

                odds_api_events = map_events(
                    [
                        {
                            "id": "66886840",
                            "home": "Fluminense",
                            "away": "Sao Paulo",
                            "startsAt": "2026-06-11T18:00:00Z",
                            "league": "Brazil - Brasileiro Serie A",
                        }
                    ]
                )
                assert len(odds_api_events) == 1, "Odds-API event payload should map to a match"
                assert odds_api_events[0].data_completeness >= 0.75, (
                    "Odds-API event fixtures with stable league/team/kickoff fields must clear secondary live tier data quality"
                )

                os.environ["ODDS_API_IO_KEY"] = "test-key"
                settings.data_sources["odds_api_io"].bookmakers = ["Bet365", "Pinnacle", "Unibet"]
                fake_http = FakeHttp()
                client = OddsApiIoClient(
                    ClientContext(
                        provider="odds_api_io",
                        source=settings.data_sources["odds_api_io"],
                        settings=settings,
                        repository=repository,
                        http=fake_http,
                    )
                )
                client.odds(event_id="66886840")
                assert fake_http.last_params["bookmakers"] == "Bet365,Pinnacle,Unibet", (
                    "Odds-API client must use configured bookmaker coverage instead of a hard-coded list"
                )
                client.odds_multi(event_ids=["66886840", "66886838"])
                assert fake_http.calls[-1]["endpoint"] == "/odds/multi"
                assert fake_http.calls[-1]["params"]["eventIds"] == "66886840,66886838"
                assert fake_http.calls[-1]["params"]["bookmakers"] == "Bet365,Pinnacle,Unibet"

                empty_events_http = FakeHttp(errors={"/events": "HTTP 404"})
                empty_events_client = OddsApiIoClient(
                    ClientContext(
                        provider="odds_api_io",
                        source=settings.data_sources["odds_api_io"],
                        settings=settings,
                        repository=repository,
                        http=empty_events_http,
                    )
                )
                assert empty_events_client.events(league="spain-la-liga") == [], (
                    "Odds-API /events 404 should be treated as no currently listed events"
                )

                ingestion_http = FakeHttp(
                    payloads={
                        "/events": [
                            {
                                "id": "66886840",
                                "home": "Fluminense",
                                "away": "Santos",
                                "startsAt": "2027-01-01T18:00:00Z",
                                "league": "Italy - Serie A",
                            },
                            {
                                "id": "66886838",
                                "home": "Milan",
                                "away": "Inter",
                                "startsAt": "2027-01-02T18:00:00Z",
                                "league": "Italy - Serie A",
                            },
                        ],
                        "/odds/multi": [
                            {
                                "id": "66886840",
                                "bookmakers": {
                                    "Bet365": [{"name": "Spread", "odds": [{"hdp": 0.5, "home": 2.1, "away": 1.8}]}],
                                },
                            },
                            {
                                "id": "66886838",
                                "bookmakers": {
                                    "Pinnacle": [{"name": "ML", "odds": [{"home": 1.9, "draw": 3.4, "away": 4.2}]}],
                                },
                            },
                        ],
                    }
                )
                service.ingestion.http = ingestion_http
                result = service.ingestion.ingest_odds(source="odds_api_io", league_code="SERIE_A", max_events=2)
                assert not result.errors, f"batch Odds-API ingestion should not error: {result.errors}"
                endpoints = [call["endpoint"] for call in ingestion_http.calls]
                assert endpoints == ["/events", "/odds/multi"], (
                    "Odds-API ingestion should batch event odds into one /odds/multi request"
                )
                assert ingestion_http.calls[-1]["params"]["eventIds"] == "66886840,66886838"

                the_odds_api_payload = [
                    {
                        "id": "toa-event-1",
                        "sport_key": "soccer_epl",
                        "sport_title": "EPL",
                        "commence_time": "2027-01-03T18:00:00Z",
                        "home_team": "Arsenal",
                        "away_team": "Chelsea",
                        "bookmakers": [
                            {
                                "key": "pinnacle",
                                "title": "Pinnacle",
                                "last_update": "2027-01-03T10:00:00Z",
                                "markets": [
                                    {
                                        "key": "h2h",
                                        "last_update": "2027-01-03T10:01:00Z",
                                        "outcomes": [
                                            {"name": "Arsenal", "price": 2.1},
                                            {"name": "Draw", "price": 3.4},
                                            {"name": "Chelsea", "price": 3.2},
                                        ],
                                    },
                                    {
                                        "key": "spreads",
                                        "outcomes": [
                                            {"name": "Arsenal", "price": 1.91, "point": -0.5},
                                            {"name": "Chelsea", "price": 1.95, "point": 0.5},
                                        ],
                                    },
                                    {
                                        "key": "totals",
                                        "outcomes": [
                                            {"name": "Over", "price": 1.88, "point": 2.5},
                                            {"name": "Under", "price": 2.02, "point": 2.5},
                                        ],
                                    },
                                ],
                            }
                        ],
                    },
                    {
                        "id": "toa-event-2",
                        "sport_key": "soccer_epl",
                        "sport_title": "EPL",
                        "commence_time": "2027-01-04T18:00:00Z",
                        "home_team": "Liverpool",
                        "away_team": "Everton",
                        "bookmakers": [],
                    },
                ]
                toa_matches, toa_snapshots = map_odds_payload(the_odds_api_payload[:1], sport_key="soccer_epl")
                assert len(toa_matches) == 1, "The Odds API event payload should map to a match"
                assert len(toa_snapshots) == 3, "The Odds API h2h/spreads/totals markets should map to snapshots"
                h2h = next(item for item in toa_snapshots if item.market_type.value == "1x2")
                assert h2h.outcome_odds == {"HOME": 2.1, "DRAW": 3.4, "AWAY": 3.2}
                spread = next(item for item in toa_snapshots if item.market_type.value == "asian_handicap")
                assert spread.line == "-0.5"
                assert spread.outcome_odds == {"HOME": 1.91, "AWAY": 1.95}
                total = next(item for item in toa_snapshots if item.market_type.value == "over_under")
                assert total.line == "2.5"
                assert total.outcome_odds == {"OVER": 1.88, "UNDER": 2.02}
                assert sport_key_for_league("EPL", "england-premier-league", {}) == "soccer_epl"
                sports_payload = [
                    {
                        "key": "soccer_epl",
                        "group": "Soccer",
                        "title": "EPL",
                        "active": True,
                        "has_outrights": False,
                    }
                ]
                assert map_sports_payload(sports_payload) == [
                    {
                        "key": "soccer_epl",
                        "group": "Soccer",
                        "title": "EPL",
                        "active": True,
                        "has_outrights": False,
                    }
                ]
                historical_payload = {
                    "timestamp": "2027-01-03T12:00:00Z",
                    "previous_timestamp": "2027-01-03T11:50:00Z",
                    "next_timestamp": "2027-01-03T12:10:00Z",
                    "data": the_odds_api_payload,
                }
                historical = map_historical_odds_payload(historical_payload, sport_key="soccer_epl")
                assert historical["timestamp"] == "2027-01-03T12:00:00Z"
                assert len(historical["matches"]) == 2
                assert len(historical["snapshots"]) == 3
                assert historical["snapshots"][0].collected_at.isoformat().startswith("2027-01-03T12:00:00")

                os.environ["THE_ODDS_API_KEY"] = "test-the-odds-api-key"
                settings.data_sources["the_odds_api"].regions = ["uk"]
                settings.data_sources["the_odds_api"].markets = ["h2h", "spreads", "totals"]
                the_odds_http = FakeHttp(
                    payloads={
                        "/sports": sports_payload,
                        "/sports/soccer_epl/odds": the_odds_api_payload,
                        "/historical/sports/soccer_epl/odds": historical_payload,
                    }
                )
                the_odds_client = TheOddsApiClient(
                    ClientContext(
                        provider="the_odds_api",
                        source=settings.data_sources["the_odds_api"],
                        settings=settings,
                        repository=repository,
                        http=the_odds_http,
                    )
                )
                assert the_odds_client.sports() == map_sports_payload(sports_payload)
                assert the_odds_http.calls[-1]["endpoint"] == "/sports"
                assert the_odds_http.calls[-1]["params"]["all"] == "true"
                assert the_odds_http.calls[-1]["params"]["apiKey"] == "test-the-odds-api-key"
                the_odds_client.odds("soccer_epl")
                assert the_odds_http.calls[-1]["endpoint"] == "/sports/soccer_epl/odds"
                assert the_odds_http.calls[-1]["params"]["apiKey"] == "test-the-odds-api-key"
                assert the_odds_http.calls[-1]["params"]["regions"] == "uk"
                assert the_odds_http.calls[-1]["params"]["markets"] == "h2h,spreads,totals"
                assert the_odds_http.calls[-1]["params"]["oddsFormat"] == "decimal"
                client_historical = the_odds_client.historical_odds(
                    "soccer_epl",
                    snapshot_time="2027-01-03T12:00:00Z",
                )
                assert client_historical["next_timestamp"] == "2027-01-03T12:10:00Z"
                assert the_odds_http.calls[-1]["endpoint"] == "/historical/sports/soccer_epl/odds"
                assert the_odds_http.calls[-1]["params"]["date"] == "2027-01-03T12:00:00Z"

                service.ingestion.http = FakeHttp(payloads={"/sports/soccer_epl/odds": the_odds_api_payload})
                result = service.ingestion.ingest_odds(source="the_odds_api", league_code="EPL", max_events=1)
                assert not result.errors, f"The Odds API ingestion should not error: {result.errors}"
                assert result.inserted == 3, "The Odds API ingestion should store selected event market snapshots"
                stored_matches = repository.list_models("matches", Match)
                assert any(getattr(match, "id", "") == "the_odds_api:toa-event-1" for match in stored_matches)
                service.ingestion.http = FakeHttp(payloads={"/historical/sports/soccer_epl/odds": historical_payload})
                historical_result = service.ingestion.ingest_historical_odds(
                    source="the_odds_api",
                    league_code="EPL",
                    snapshot_time="2027-01-03T12:00:00Z",
                    max_events=1,
                )
                assert not historical_result.errors, (
                    f"The Odds API historical odds ingestion should not error: {historical_result.errors}"
                )
                assert historical_result.inserted == 3

                sportmonks_fixture_payload = {
                    "data": [
                        {
                            "id": 111,
                            "name": "Arsenal vs Chelsea",
                            "starting_at": "2027-01-03T18:00:00Z",
                            "league_id": 8,
                            "league": {"id": 8, "name": "Premier League"},
                            "state": {"short_name": "NS"},
                            "participants": [
                                {"id": 1, "name": "Arsenal", "meta": {"location": "home"}},
                                {"id": 2, "name": "Chelsea", "meta": {"location": "away"}},
                            ],
                        }
                    ]
                }
                sportmonks_odds_payload = {
                    "data": [
                        {
                            "fixture_id": 111,
                            "market_id": 1,
                            "market": {"id": 1, "name": "Fulltime Result"},
                            "bookmaker_id": 10,
                            "bookmaker": {"id": 10, "name": "Bet365"},
                            "label": "1",
                            "odds": "2.10",
                        },
                        {
                            "fixture_id": 111,
                            "market_id": 1,
                            "market": {"id": 1, "name": "Fulltime Result"},
                            "bookmaker_id": 10,
                            "bookmaker": {"id": 10, "name": "Bet365"},
                            "label": "X",
                            "odds": "3.40",
                        },
                        {
                            "fixture_id": 111,
                            "market_id": 1,
                            "market": {"id": 1, "name": "Fulltime Result"},
                            "bookmaker_id": 10,
                            "bookmaker": {"id": 10, "name": "Bet365"},
                            "label": "2",
                            "odds": "3.20",
                        },
                        {
                            "fixture_id": 111,
                            "market_id": 2,
                            "market": {"id": 2, "name": "Over/Under 2.5 Goals"},
                            "bookmaker_id": 10,
                            "bookmaker": {"id": 10, "name": "Bet365"},
                            "label": "Over",
                            "total": "2.5",
                            "odds": "1.88",
                        },
                        {
                            "fixture_id": 111,
                            "market_id": 2,
                            "market": {"id": 2, "name": "Over/Under 2.5 Goals"},
                            "bookmaker_id": 10,
                            "bookmaker": {"id": 10, "name": "Bet365"},
                            "label": "Under",
                            "total": "2.5",
                            "odds": "2.02",
                        },
                    ]
                }
                sportmonks_matches = map_sportmonks_fixtures_payload(sportmonks_fixture_payload)
                assert len(sportmonks_matches) == 1, "Sportmonks fixture payload should map to a match"
                assert sportmonks_matches[0].id == "sportmonks:111"
                assert sportmonks_matches[0].home_team == "Arsenal"
                assert sportmonks_matches[0].away_team == "Chelsea"
                assert sportmonks_matches[0].external_ids["sportmonks_league"] == "8"
                sportmonks_snapshots = map_sportmonks_pre_match_odds_payload(
                    sportmonks_odds_payload,
                    fixture_id="111",
                )
                assert len(sportmonks_snapshots) == 2, "Sportmonks 1x2 and totals odds should map to snapshots"
                sportmonks_h2h = next(item for item in sportmonks_snapshots if item.market_type.value == "1x2")
                assert sportmonks_h2h.outcome_odds == {"HOME": 2.1, "DRAW": 3.4, "AWAY": 3.2}
                sportmonks_total = next(item for item in sportmonks_snapshots if item.market_type.value == "over_under")
                assert sportmonks_total.line == "2.5"
                assert sportmonks_total.outcome_odds == {"OVER": 1.88, "UNDER": 2.02}

                os.environ["SPORTMONKS_TOKEN"] = "test-sportmonks-token"
                settings.data_sources["sportmonks"].enabled = True
                epl = next(item for item in settings.leagues if item.code == "EPL")
                epl.sportmonks_league_id = 8
                sportmonks_http = FakeHttp(
                    payloads={
                        "/fixtures/date/2027-01-03": sportmonks_fixture_payload,
                        "/odds/pre-match/fixtures/111": sportmonks_odds_payload,
                    }
                )
                sportmonks_client = SportmonksClient(
                    ClientContext(
                        provider="sportmonks",
                        source=settings.data_sources["sportmonks"],
                        settings=settings,
                        repository=repository,
                        http=sportmonks_http,
                    )
                )
                sportmonks_client.fixtures("2027-01-03", league_id=8)
                assert sportmonks_http.calls[-1]["endpoint"] == "/fixtures/date/2027-01-03"
                assert sportmonks_http.calls[-1]["params"]["api_token"] == "test-sportmonks-token"
                assert sportmonks_http.calls[-1]["params"]["filters"] == "fixtureLeagues:8"
                sportmonks_client.odds_by_fixture("111")
                assert sportmonks_http.calls[-1]["endpoint"] == "/odds/pre-match/fixtures/111"
                assert sportmonks_http.calls[-1]["params"]["api_token"] == "test-sportmonks-token"

                service.ingestion.http = FakeHttp(
                    payloads={
                        "/fixtures/date/2027-01-03": sportmonks_fixture_payload,
                        "/odds/pre-match/fixtures/111": sportmonks_odds_payload,
                    }
                )
                sportmonks_result = service.ingestion.ingest_odds(
                    source="sportmonks",
                    league_code="EPL",
                    date="2027-01-03",
                    max_events=1,
                )
                assert not sportmonks_result.errors, (
                    f"Sportmonks odds ingestion should not error: {sportmonks_result.errors}"
                )
                assert sportmonks_result.inserted == 2
            finally:
                repository.close()
    finally:
        if previous is None:
            os.environ.pop("FOOTBALL_VALIDATE_REMOTE", None)
        else:
            os.environ["FOOTBALL_VALIDATE_REMOTE"] = previous

    print("datasource verification passed")


class FakeHttp:
    def __init__(self, payloads: dict | None = None, errors: dict | None = None) -> None:
        self.last_params: dict = {}
        self.calls: list[dict] = []
        self.payloads = payloads or {}
        self.errors = errors or {}

    def get_json(self, **kwargs):
        self.last_params = kwargs["params"]
        self.calls.append(kwargs)
        endpoint = kwargs["endpoint"]
        return SourceResponse(
            provider="odds_api_io",
            endpoint=endpoint,
            request_key="fake",
            status_code=404 if endpoint in self.errors else 200,
            payload=self.payloads.get(endpoint, {"data": []}),
            error=self.errors.get(endpoint),
        )

    def get_text(self, **kwargs):
        self.last_params = kwargs.get("params", {})
        self.calls.append(kwargs)
        endpoint = kwargs["endpoint"]
        return SourceResponse(
            provider=kwargs["provider"],
            endpoint=endpoint,
            request_key="fake",
            status_code=404 if endpoint in self.errors else 200,
            payload=self.payloads.get(endpoint, ""),
            error=self.errors.get(endpoint),
        )


if __name__ == "__main__":
    main()
