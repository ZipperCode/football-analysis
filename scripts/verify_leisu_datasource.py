from __future__ import annotations

import base64
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from Crypto.Cipher import AES

from football_analysis.contracts import SourceResponse
from football_analysis.datasources.base import ClientContext
from football_analysis.datasources.leisu import (
    DEFAULT_AES_KEY,
    LeisuClient,
    decode_config_payload,
    generate_auth_key,
    generate_sign,
    map_fixtures,
    map_odds,
)
from football_analysis.db import StructuredRepository
from football_analysis.ingestion import IngestionService
from football_analysis.models import MarketType, Match, MatchStatus
from football_analysis.settings import load_settings
from football_analysis.datasources.base import DataSourceError


def main() -> None:
    assert generate_sign("", "2026-06-22 12:00:00") == "4eab24a24759294ed1747be48c9f0d31"
    assert (
        generate_auth_key("/v1/app/match/football/match_live", "url-key", timestamp=1782140000, uid="12345678123456781234567812345678")
        == "1782140000-12345678123456781234567812345678-0-06b1d63324ddb5f44ca5bd40bcfff0b4"
    )

    config = {
        "gateway_url": "https://gateway.example",
        "gateway_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "other": {"rk": _encrypt_zero_padded("runtime-url-key")},
    }
    encoded_config = _encrypt_zero_padded(json.dumps(config, ensure_ascii=False))
    decoded = decode_config_payload({"code": 1, "data": encoded_config})
    assert decoded["gateway_url"] == "https://gateway.example"

    fixture_payload = {
        "data": {
            "list": [
                {
                    "match_id": "1001",
                    "competition_name": "FIFA World Cup",
                    "home_name": "Home FC",
                    "away_name": "Away FC",
                    "match_time": "2026-06-22 20:00:00",
                    "status": "Fixture",
                }
            ]
        }
    }
    matches = map_fixtures(fixture_payload)
    assert len(matches) == 1
    assert matches[0].id == "leisu:1001"
    assert matches[0].external_ids["leisu_match"] == "1001"

    odds = map_odds(
        {
            "data": {
                "list": [
                    {"company_name": "Book", "home": 2.1, "draw": 3.2, "away": 3.4},
                    {"company_name": "Book", "over": 1.95, "line": 2.5, "under": 1.9},
                ]
            }
        },
        match_id="1001",
    )
    assert {snapshot.market_type for snapshot in odds} == {MarketType.one_x_two, MarketType.over_under}

    settings = load_settings()
    with tempfile.TemporaryDirectory() as tmp:
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'leisu-disabled.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            LeisuClient(ClientContext("leisu", settings.data_sources["leisu"], settings, repository, FakeHttp({})))
            raise AssertionError("disabled leisu source should be rejected")
        except DataSourceError as exc:
            assert str(exc) == "leisu_source_disabled"
        finally:
            repository.close()

    settings.data_sources["leisu"].enabled = True
    with tempfile.TemporaryDirectory() as tmp:
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'leisu.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        service = IngestionService(settings, repository)
        service.http = FakeHttp(
            {
                "/v1/app/leisu/info": {"code": 1, "data": encoded_config},
                "/v1/app/match/common/odds_list": {"data": {"list": [{"company_name": "Book", "home": 2.1, "draw": 3.2, "away": 3.4}]}},
            }
        )
        repository.upsert_model(
            "matches",
            "leisu:1001",
            Match(
                id="leisu:1001",
                league="FIFA World Cup",
                home_team="Home FC",
                away_team="Away FC",
                kickoff_at=datetime(2026, 6, 22, 20, 0, 0),
                status=MatchStatus.scheduled,
                external_ids={"leisu_match": "1001"},
            ),
        )
        result = service.ingest_odds(source="leisu", max_events=1)
        assert not result.errors, result.errors
        assert result.inserted == 1
        assert service.http.calls[-1]["endpoint"] == "/v1/app/match/common/odds_list"
        repository.close()

    if os.getenv("FOOTBALL_VALIDATE_REMOTE") == "1":
        settings = load_settings()
        settings.data_sources["leisu"].enabled = True
        with tempfile.TemporaryDirectory() as tmp:
            settings.storage.database_url = f"sqlite:///{Path(tmp) / 'leisu-remote.db'}"
            repository = StructuredRepository(settings.storage.database_url)
            repository.initialize()
            context = ClientContext("leisu", settings.data_sources["leisu"], settings, repository, IngestionService(settings, repository).http)
            LeisuClient(context).sync_config()
            repository.close()

    print("leisu datasource verification passed")


class FakeHttp:
    def __init__(self, payloads: dict[str, dict]) -> None:
        self.payloads = payloads
        self.calls: list[dict] = []

    def get_json(self, **kwargs):
        self.calls.append(kwargs)
        endpoint = kwargs["endpoint"]
        return SourceResponse(
            provider=kwargs["provider"],
            endpoint=endpoint,
            request_key="fake",
            status_code=200,
            payload=self.payloads.get(endpoint, {"data": []}),
        )


def _encrypt_zero_padded(value: str) -> str:
    raw = value.encode("utf-8")
    padding = (16 - len(raw) % 16) % 16
    padded = raw + b"\x00" * padding
    return base64.b64encode(AES.new(DEFAULT_AES_KEY, AES.MODE_ECB).encrypt(padded)).decode("ascii")


if __name__ == "__main__":
    main()
