from __future__ import annotations

from datetime import datetime
from typing import Any

from football_analysis.datasources.base import ClientContext, DataSourceError
from football_analysis.models import Match, MatchStatus


class FootballDataOrgClient:
    provider = "football_data_org"

    def __init__(self, context: ClientContext):
        self.context = context

    def matches(self, date_from: str | None = None, date_to: str | None = None, competition: str | None = None) -> list[Match]:
        if competition:
            endpoint = f"/competitions/{competition}/matches"
        else:
            endpoint = "/matches"
        payload = self._get(endpoint, {"dateFrom": date_from, "dateTo": date_to})
        return map_matches(payload)

    def standings(self, competition: str) -> dict[str, Any]:
        return self._get(f"/competitions/{competition}/standings", {})

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.context.api_key:
            raise DataSourceError("missing_credentials:FOOTBALL_DATA_ORG_TOKEN")
        response = self.context.http.get_json(
            provider=self.provider,
            url=f"{self.context.source.base_url.rstrip('/')}{endpoint}",
            endpoint=endpoint,
            headers={"X-Auth-Token": self.context.api_key},
            params={key: value for key, value in params.items() if value is not None},
        )
        if response.error:
            raise DataSourceError(response.error)
        if not isinstance(response.payload, dict):
            raise DataSourceError("invalid_payload:expected_object")
        return response.payload


def map_matches(payload: dict[str, Any]) -> list[Match]:
    matches: list[Match] = []
    for item in payload.get("matches", []) or []:
        match_id = str(item.get("id"))
        if not match_id or match_id == "None":
            continue
        home = item.get("homeTeam") or {}
        away = item.get("awayTeam") or {}
        competition = item.get("competition") or {}
        matches.append(
            Match(
                id=f"football_data_org:{match_id}",
                league=str(competition.get("name") or "Unknown"),
                home_team=str(home.get("name") or "Unknown Home"),
                away_team=str(away.get("name") or "Unknown Away"),
                kickoff_at=_parse_datetime(str(item.get("utcDate"))),
                status=_map_status(str(item.get("status") or "SCHEDULED")),
                data_completeness=0.62,
                external_ids={
                    "football_data_org_match": match_id,
                    "football_data_org_competition": str(competition.get("code") or competition.get("id") or ""),
                    "football_data_org_home_team": str(home.get("id") or ""),
                    "football_data_org_away_team": str(away.get("id") or ""),
                },
            )
        )
    return matches


def _parse_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _map_status(status: str) -> MatchStatus:
    if status in {"FINISHED"}:
        return MatchStatus.finished
    if status in {"POSTPONED", "SUSPENDED", "CANCELLED"}:
        return MatchStatus.postponed
    return MatchStatus.scheduled
