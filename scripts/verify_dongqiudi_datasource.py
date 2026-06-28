from __future__ import annotations

import tempfile
from pathlib import Path

from football_analysis.contracts import SourceResponse
from football_analysis.datasources.base import ClientContext
from football_analysis.datasources.dongqiudi import DongqiudiClient, map_fixtures
from football_analysis.db import StructuredRepository
from football_analysis.ingestion import IngestionService
from football_analysis.models import AgentFinding, MatchStatus
from football_analysis.settings import load_settings


def main() -> None:
    payload = {
        "matchList": [
            {
                "match_id": "54328044",
                "team_A_name": "Switzerland",
                "team_A_id": "100",
                "team_B_name": "Canada",
                "team_B_id": "200",
                "competition_name": "FIFA World Cup",
                "start_play": "2026-06-25 03:00:00",
                "status": "Fixture",
            }
        ]
    }
    matches = map_fixtures(payload)
    assert len(matches) == 1
    assert matches[0].id == "dongqiudi:54328044"
    assert matches[0].external_ids["dongqiudi_match"] == "54328044"
    assert matches[0].external_ids["dongqiudi_home_team"] == "100"
    assert matches[0].external_ids["dongqiudi_away_team"] == "200"

    settings = load_settings()
    assert settings.data_sources["dongqiudi"].enabled is True, "dongqiudi should be enabled by default"

    with tempfile.TemporaryDirectory() as tmp:
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'dongqiudi.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        service = IngestionService(settings, repository)
        service.http = FakeHttp(
            {
                "/data/tab/new": payload,
                "/mobile/match/analysis/54328044": {"data": {"recent": "home strong"}},
                "/mobile/match/lineup/54328044": {"data": {"home": ["A"], "away": ["B"]}},
                "/mobile/match/situation/54328044": {"data": {"possession": [52, 48]}},
                "/poll": {"data": {"status": "Fixture"}},
                "/v3/archive/app/channel/feeds": {"data": {"articles": [{"id": "a1", "title": "Team news"}]}},
                "/v2/article/detail/a1": {"data": {"id": "a1", "title": "Team news", "body": "Full team news"}},
            }
        )
        result = service.ingest_fixtures(date="2026-06-25", source="dongqiudi")
        assert not result.errors, result.errors
        assert result.inserted == 1
        assert service.http.calls[-1]["params"]["version"] == 576

        context = ClientContext("dongqiudi", settings.data_sources["dongqiudi"], settings, repository, service.http)
        assert DongqiudiClient(context).fixtures("2026-06-25")[0].id == "dongqiudi:54328044"
        intelligence = service.ingest_intelligence(source="dongqiudi", match_id="dongqiudi:54328044")
        assert not intelligence.errors, intelligence.errors
        assert intelligence.inserted == 6
        findings = repository.list_models("findings", AgentFinding)
        assert {finding.agent_name for finding in findings} == {
            "dongqiudi_match_analysis",
            "dongqiudi_lineup",
            "dongqiudi_situation",
            "dongqiudi_poll",
            "dongqiudi_home_team_feeds",
            "dongqiudi_away_team_feeds",
        }
        assert all(finding.score_delta == 0 for finding in findings)
        assert all(finding.evidence_sources for finding in findings)
        feed_findings = [finding for finding in findings if finding.agent_name.endswith("_team_feeds")]
        assert all(finding.payload["payload"]["article_details"] for finding in feed_findings)
        assert any(
            call["endpoint"] == "/v3/archive/app/channel/feeds" and call["params"]["platform"] == "web"
            for call in service.http.calls
        )

        service.http = FakeHttp(
            {
                "/mobile/match/analysis/54328044": RuntimeError("timeout"),
                "/mobile/match/lineup/54328044": {"data": {"home": ["A"]}},
                "/mobile/match/situation/54328044": {"data": {}},
                "/poll": {"data": {"status": "Fixture"}},
            }
        )
        partial = service.ingest_intelligence(source="dongqiudi", match_id="dongqiudi:54328044", include_team_feeds=False)
        assert partial.errors, "single endpoint failure should be reported"
        assert partial.inserted == 2, partial.model_dump()
        assert partial.job.status.value == "partial"

        stored_match = repository.get_model("matches", "dongqiudi:54328044", type(matches[0]))
        assert stored_match is not None
        stored_match.status = MatchStatus.finished
        repository.upsert_model("matches", stored_match.id, stored_match)
        service.http = FakeHttp(
            {
                "/mobile/match/analysis/54328044": {"data": {"recent": "finished"}},
                "/mobile/match/lineup/54328044": {"data": {"home": ["A"]}},
                "/mobile/match/situation/54328044": {"data": {"possession": [50, 50]}},
                "/poll": {"data": {"status": "Played"}},
                "/mobile/match/highlights/54328044": {"data": {"clips": [{"title": "Goal"}]}},
                "/v3/archive/app/channel/feeds": {"data": {"articles": [{"id": "a2", "title": "Post match"}]}},
                "/v2/article/detail/a2": RuntimeError("article timeout"),
            }
        )
        completed = service.ingest_intelligence(
            source="dongqiudi",
            match_id="dongqiudi:54328044",
            article_detail_limit=1,
        )
        assert completed.errors, "article detail failure should be reported"
        assert completed.job.status.value == "partial"
        assert any(
            finding.agent_name == "dongqiudi_highlights"
            for finding in repository.list_models("findings", AgentFinding)
        )
        repository.close()

    print("dongqiudi datasource verification passed")


class FakeHttp:
    def __init__(self, payloads: dict[str, dict]) -> None:
        self.payloads = payloads
        self.calls: list[dict] = []

    def get_json(self, **kwargs):
        self.calls.append(kwargs)
        endpoint = kwargs["endpoint"]
        payload = self.payloads.get(endpoint, {})
        if isinstance(payload, Exception):
            return SourceResponse(
                provider=kwargs["provider"],
                endpoint=endpoint,
                request_key="fake",
                status_code=None,
                error=f"{type(payload).__name__}: {payload}",
            )
        return SourceResponse(
            provider=kwargs["provider"],
            endpoint=endpoint,
            request_key="fake",
            status_code=200,
            payload=payload,
        )


if __name__ == "__main__":
    main()
