from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from football_analysis.contracts import SourceResponse
from football_analysis.datasources.base import ClientContext
from football_analysis.datasources.football_data_uk import parse_csv_text
from football_analysis.datasources.odds_api_io import OddsApiIoClient, map_events, map_odds
from football_analysis.db import StructuredRepository
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
                world_cup_event = map_events(
                    [
                        {
                            "id": "66457050",
                            "home": "Panama",
                            "away": "England",
                            "startsAt": "2026-06-27T18:00:00Z",
                            "league": "International - FIFA World Cup",
                        }
                    ]
                )[0]
                assert world_cup_event.data_completeness >= 0.82, (
                    "structured Odds-API major tournament fixtures must clear live data quality when teams, league, "
                    "kickoff, and provider event id are all present"
                )
                mixed_market_odds = map_odds(
                    [
                        {
                            "id": "66457026",
                            "bookmakers": {
                                "1xbet": [
                                    {"name": "Totals", "odds": [{"hdp": 2.5, "over": "1.45", "under": "2.48"}]},
                                    {
                                        "name": "Alternative Corners",
                                        "odds": [{"hdp": 2.5, "over": "50.0", "under": "1.002"}],
                                    },
                                    {
                                        "name": "Team Total Away",
                                        "odds": [{"hdp": 2.5, "over": "1.81", "under": "2.01"}],
                                    },
                                    {
                                        "name": "Alternative Asian Handicap",
                                        "odds": [{"hdp": -3.25, "home": "6.8", "away": "1.105"}],
                                    },
                                ],
                                "Bet365": [
                                    {"name": "Goals Over/Under", "odds": [{"hdp": 2.5, "over": "1.50", "under": "2.625"}]},
                                    {"name": "Spread", "odds": [{"hdp": 0.25, "home": "1.70", "away": "2.20"}]},
                                ],
                            },
                        }
                    ]
                )
                assert len(mixed_market_odds) == 3, "Odds-API should keep only core full-time 1X2/AH/totals markets"
                assert {item.id for item in mixed_market_odds} == {
                    "odds_api_io:66457026:1xbet:over_under:2.5",
                    "odds_api_io:66457026:Bet365:over_under:2.5",
                    "odds_api_io:66457026:Bet365:asian_handicap:0.25",
                }

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
            finally:
                repository.close()
    finally:
        if previous is None:
            os.environ.pop("FOOTBALL_VALIDATE_REMOTE", None)
        else:
            os.environ["FOOTBALL_VALIDATE_REMOTE"] = previous

    print("datasource verification passed")


class FakeHttp:
    def __init__(self, payloads: dict | None = None) -> None:
        self.last_params: dict = {}
        self.calls: list[dict] = []
        self.payloads = payloads or {}

    def get_json(self, **kwargs):
        self.last_params = kwargs["params"]
        self.calls.append(kwargs)
        endpoint = kwargs["endpoint"]
        return SourceResponse(
            provider="odds_api_io",
            endpoint=endpoint,
            request_key="fake",
            payload=self.payloads.get(endpoint, {"data": []}),
        )


if __name__ == "__main__":
    main()
