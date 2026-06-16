from __future__ import annotations

from datetime import datetime
from typing import Any

from football_analysis.datasources.base import ClientContext, DataSourceError
from football_analysis.models import Match, MatchStatus, MarketType, OddsSnapshot


class SportmonksClient:
    provider = "sportmonks"

    def __init__(self, context: ClientContext):
        self.context = context

    def fixtures(self, date: str, league_id: int | None = None) -> list[Match]:
        params: dict[str, Any] = {
            "include": "participants;league;scores;state",
        }
        if league_id is not None:
            params["filters"] = f"fixtureLeagues:{league_id}"
        payload = self._get(f"/fixtures/date/{date}", params)
        return map_fixtures_payload(payload)

    def odds_by_fixture(self, fixture_id: str | int) -> list[OddsSnapshot]:
        fixture_key = str(fixture_id)
        payload = self._get(
            f"/odds/pre-match/fixtures/{fixture_key}",
            {"include": "bookmaker;market"},
        )
        return map_pre_match_odds_payload(payload, fixture_id=fixture_key)

    def _get(self, endpoint: str, params: dict[str, Any]) -> Any:
        if not self.context.api_key:
            raise DataSourceError("missing_credentials:SPORTMONKS_TOKEN")
        clean_params = {key: value for key, value in params.items() if value is not None}
        clean_params["api_token"] = self.context.api_key
        response = self.context.http.get_json(
            provider=self.provider,
            url=f"{self.context.source.base_url.rstrip('/')}{endpoint}",
            endpoint=endpoint,
            params=clean_params,
        )
        if response.error:
            raise DataSourceError(response.error)
        return response.payload


def map_fixtures_payload(payload: Any) -> list[Match]:
    rows = payload.get("data", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    matches: list[Match] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        fixture_id = str(item.get("id") or "").strip()
        kickoff = item.get("starting_at") or item.get("starting_at_timestamp")
        if not fixture_id or not kickoff:
            continue
        home, away, external_team_ids = _participants(item)
        if not home or not away:
            home, away = _split_fixture_name(str(item.get("name") or ""))
        scores = _scores(item)
        league = item.get("league") if isinstance(item.get("league"), dict) else {}
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        matches.append(
            Match(
                id=f"sportmonks:{fixture_id}",
                league=str(league.get("name") or item.get("league_name") or "Unknown"),
                home_team=home or "Unknown Home",
                away_team=away or "Unknown Away",
                kickoff_at=_parse_datetime(kickoff),
                status=_map_status(str(state.get("short_name") or state.get("name") or item.get("state_id") or "")),
                data_completeness=0.78,
                season=_safe_int(item.get("season_id")),
                home_score=scores.get("home"),
                away_score=scores.get("away"),
                external_ids={
                    "sportmonks_fixture": fixture_id,
                    "sportmonks_league": str(item.get("league_id") or league.get("id") or ""),
                    **external_team_ids,
                },
            )
        )
    return matches


def map_pre_match_odds_payload(payload: Any, fixture_id: str | int) -> list[OddsSnapshot]:
    rows = payload.get("data", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    fixture_key = str(fixture_id)
    grouped: dict[tuple[str, str, MarketType, str], dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        market_type = _market_type(item)
        if market_type is None:
            continue
        selection = _selection(item, market_type)
        price = _safe_float(item.get("odds") or item.get("decimal") or item.get("price") or item.get("value"))
        if not selection or price is None:
            continue
        bookmaker = item.get("bookmaker") if isinstance(item.get("bookmaker"), dict) else {}
        market = item.get("market") if isinstance(item.get("market"), dict) else {}
        bookmaker_key = str(item.get("bookmaker_id") or bookmaker.get("id") or bookmaker.get("name") or "unknown")
        bookmaker_name = str(bookmaker.get("name") or item.get("bookmaker_name") or bookmaker_key)
        line = _line(item, market_type)
        key = (bookmaker_key, bookmaker_name, market_type, line or "main")
        bucket = grouped.setdefault(
            key,
            {
                "outcome_odds": {},
                "market_id": str(item.get("market_id") or market.get("id") or market_type.value),
                "line": line,
            },
        )
        bucket["outcome_odds"][selection] = price

    snapshots: list[OddsSnapshot] = []
    for (bookmaker_key, bookmaker_name, market_type, line_key), bucket in grouped.items():
        odds = bucket["outcome_odds"]
        if len(odds) < 2:
            continue
        snapshots.append(
            OddsSnapshot(
                id=f"sportmonks:{fixture_key}:{bookmaker_key}:{bucket['market_id']}:{market_type.value}:{line_key}",
                match_id=f"sportmonks:{fixture_key}",
                market_type=market_type,
                line=bucket.get("line"),
                source="sportmonks",
                bookmaker=bookmaker_name,
                collected_at=datetime.utcnow(),
                outcome_odds=odds,
                best_price=dict(odds),
            )
        )
    return snapshots


def _participants(item: dict[str, Any]) -> tuple[str | None, str | None, dict[str, str]]:
    home: str | None = None
    away: str | None = None
    external: dict[str, str] = {}
    participants = item.get("participants") if isinstance(item.get("participants"), list) else []
    for participant in participants:
        if not isinstance(participant, dict):
            continue
        name = str(participant.get("name") or "").strip()
        team_id = str(participant.get("id") or "").strip()
        meta = participant.get("meta") if isinstance(participant.get("meta"), dict) else {}
        location = str(meta.get("location") or participant.get("location") or "").lower()
        if location == "home":
            home = name
            if team_id:
                external["sportmonks_home_team"] = team_id
        elif location == "away":
            away = name
            if team_id:
                external["sportmonks_away_team"] = team_id
    return home, away, external


def _split_fixture_name(value: str) -> tuple[str | None, str | None]:
    for separator in (" vs ", " v ", " - "):
        if separator in value:
            home, away = value.split(separator, 1)
            return home.strip(), away.strip()
    return None, None


def _scores(item: dict[str, Any]) -> dict[str, int | None]:
    scores = item.get("scores") if isinstance(item.get("scores"), list) else []
    values: dict[str, int | None] = {"home": None, "away": None}
    for score in scores:
        if not isinstance(score, dict):
            continue
        participant = str(score.get("score", {}).get("participant") if isinstance(score.get("score"), dict) else "")
        goals = _safe_int((score.get("score") or {}).get("goals") if isinstance(score.get("score"), dict) else None)
        if participant.lower() in {"home", "localteam"}:
            values["home"] = goals
        elif participant.lower() in {"away", "visitorteam"}:
            values["away"] = goals
    return values


def _market_type(item: dict[str, Any]) -> MarketType | None:
    market = item.get("market") if isinstance(item.get("market"), dict) else {}
    value = " ".join(
        str(part or "")
        for part in (
            market.get("name"),
            item.get("market_name"),
            item.get("name"),
            item.get("label"),
        )
    ).lower()
    if "1x2" in value or "3way" in value or "match winner" in value or "fulltime result" in value:
        return MarketType.one_x_two
    if "asian handicap" in value or "handicap" in value:
        return MarketType.asian_handicap
    if "over/under" in value or "total" in value or "goals over" in value:
        return MarketType.over_under
    return None


def _selection(item: dict[str, Any], market_type: MarketType) -> str | None:
    token = str(item.get("label") or item.get("name") or item.get("market_description") or "").strip()
    lowered = token.lower()
    if market_type == MarketType.one_x_two:
        if lowered in {"1", "home", "localteam"}:
            return "HOME"
        if lowered in {"x", "draw"}:
            return "DRAW"
        if lowered in {"2", "away", "visitorteam"}:
            return "AWAY"
    if market_type == MarketType.asian_handicap:
        if lowered in {"1", "home", "localteam"} or "home" in lowered:
            return "HOME"
        if lowered in {"2", "away", "visitorteam"} or "away" in lowered:
            return "AWAY"
    if market_type == MarketType.over_under:
        if "over" in lowered:
            return "OVER"
        if "under" in lowered:
            return "UNDER"
    return None


def _line(item: dict[str, Any], market_type: MarketType) -> str | None:
    if market_type not in {MarketType.asian_handicap, MarketType.over_under}:
        return None
    for key in ("handicap", "total", "line", "points"):
        value = item.get(key)
        if value not in {None, ""}:
            return str(value)
    return None


def _map_status(value: str) -> MatchStatus:
    lowered = value.lower()
    if lowered in {"ft", "finished", "ended", "fulltime", "5", "8"}:
        return MatchStatus.finished
    if lowered in {"postponed", "cancelled", "abandoned", "11", "12", "13"}:
        return MatchStatus.postponed
    return MatchStatus.scheduled


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


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
