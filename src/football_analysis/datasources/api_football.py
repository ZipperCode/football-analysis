from __future__ import annotations

from datetime import datetime
from typing import Any

from football_analysis.datasources.base import ClientContext, DataSourceError
from football_analysis.models import Match, MatchStatus, OddsSnapshot


class APIFootballClient:
    provider = "api_football"

    def __init__(self, context: ClientContext):
        self.context = context

    def fixtures(self, date: str, league: int | None = None, season: int | None = None) -> list[Match]:
        payload = self._get("/fixtures", {"date": date, "league": league, "season": season})
        return map_fixtures(payload)

    def odds(self, date: str | None = None, fixture: str | None = None, league: int | None = None, season: int | None = None) -> list[OddsSnapshot]:
        params = {"date": date, "fixture": fixture, "league": league, "season": season}
        payload = self._get("/odds", params)
        return map_odds(payload)

    def standings(self, league: int, season: int) -> dict[str, Any]:
        return self._get("/standings", {"league": league, "season": season})

    def injuries(self, date: str | None = None, fixture: str | None = None, league: int | None = None, season: int | None = None) -> dict[str, Any]:
        return self._get("/injuries", {"date": date, "fixture": fixture, "league": league, "season": season})

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.context.api_key:
            raise DataSourceError("missing_credentials:API_FOOTBALL_KEY")
        clean_params = {key: value for key, value in params.items() if value is not None}
        response = self.context.http.get_json(
            provider=self.provider,
            url=f"{self.context.source.base_url.rstrip('/')}{endpoint}",
            endpoint=endpoint,
            headers={"x-apisports-key": self.context.api_key},
            params=clean_params,
        )
        if response.error:
            raise DataSourceError(response.error)
        if not isinstance(response.payload, dict):
            raise DataSourceError("invalid_payload:expected_object")
        return response.payload


def map_fixtures(payload: dict[str, Any]) -> list[Match]:
    matches: list[Match] = []
    for item in payload.get("response", []) or []:
        fixture = item.get("fixture") or {}
        league = item.get("league") or {}
        teams = item.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        goals = item.get("goals") or {}
        fixture_id = str(fixture.get("id"))
        if not fixture_id or fixture_id == "None":
            continue
        matches.append(
            Match(
                id=f"api_football:{fixture_id}",
                league=str(league.get("name") or "Unknown"),
                home_team=str(home.get("name") or "Unknown Home"),
                away_team=str(away.get("name") or "Unknown Away"),
                kickoff_at=_parse_datetime(str(fixture.get("date"))),
                status=_map_status(str((fixture.get("status") or {}).get("short") or "NS")),
                data_completeness=0.72,
                season=_safe_int(league.get("season")),
                country=league.get("country"),
                home_score=_safe_int(goals.get("home")),
                away_score=_safe_int(goals.get("away")),
                external_ids={
                    "api_football_fixture": fixture_id,
                    "api_football_league": str(league.get("id") or ""),
                    "api_football_home_team": str(home.get("id") or ""),
                    "api_football_away_team": str(away.get("id") or ""),
                },
            )
        )
    return matches


def map_odds(payload: dict[str, Any]) -> list[OddsSnapshot]:
    snapshots: list[OddsSnapshot] = []
    for item in payload.get("response", []) or []:
        fixture = item.get("fixture") or {}
        fixture_id = str(fixture.get("id"))
        if not fixture_id or fixture_id == "None":
            continue
        match_id = f"api_football:{fixture_id}"
        for bookmaker in item.get("bookmakers", []) or []:
            bookmaker_name = str(bookmaker.get("name") or bookmaker.get("id") or "Unknown bookmaker")
            for bet in bookmaker.get("bets", []) or []:
                market_type = _market_type(str(bet.get("name") or ""))
                if market_type is None:
                    continue
                odds: dict[str, float] = {}
                line: str | None = None
                for value in bet.get("values", []) or []:
                    selection = _selection(str(value.get("value") or ""))
                    odd = _safe_float(value.get("odd"))
                    if odd is None or not selection:
                        continue
                    if ":" in selection:
                        selection, line = selection.split(":", 1)
                    odds[selection] = odd
                if odds:
                    snapshot_id = f"api_football:{fixture_id}:{bookmaker.get('id') or bookmaker_name}:{market_type.value}:{line or 'main'}"
                    snapshots.append(
                        OddsSnapshot(
                            id=snapshot_id,
                            match_id=match_id,
                            market_type=market_type,
                            line=line,
                            source="api_football",
                            bookmaker=bookmaker_name,
                            collected_at=datetime.utcnow(),
                            outcome_odds=odds,
                            best_price=dict(odds),
                        )
                    )
    return snapshots


def _market_type(name: str):
    from football_analysis.models import MarketType

    lowered = name.lower()
    if "match winner" in lowered or name in {"1x2", "Fulltime Result"}:
        return MarketType.one_x_two
    if "asian handicap" in lowered:
        return MarketType.asian_handicap
    if "over/under" in lowered or "goals over" in lowered:
        return MarketType.over_under
    return None


def _selection(value: str) -> str:
    normalized = value.strip()
    mapping = {"Home": "HOME", "Draw": "DRAW", "Away": "AWAY"}
    if normalized in mapping:
        return mapping[normalized]
    if normalized.startswith("Over "):
        return f"OVER:{normalized.removeprefix('Over ').strip()}"
    if normalized.startswith("Under "):
        return f"UNDER:{normalized.removeprefix('Under ').strip()}"
    return normalized.upper().replace(" ", "_")


def _map_status(short: str) -> MatchStatus:
    if short in {"FT", "AET", "PEN"}:
        return MatchStatus.finished
    if short in {"PST", "CANC", "ABD"}:
        return MatchStatus.postponed
    return MatchStatus.scheduled


def _parse_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
