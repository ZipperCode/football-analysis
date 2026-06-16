from __future__ import annotations

from datetime import datetime
from typing import Any

from football_analysis.datasources.base import ClientContext, DataSourceError
from football_analysis.models import Match, MarketType, OddsSnapshot


class TheOddsApiClient:
    provider = "the_odds_api"

    def __init__(self, context: ClientContext):
        self.context = context

    def odds(
        self,
        sport_key: str,
        regions: list[str] | None = None,
        markets: list[str] | None = None,
        bookmakers: list[str] | None = None,
    ) -> tuple[list[Match], list[OddsSnapshot]]:
        if not sport_key:
            raise DataSourceError("missing_sport_key:the_odds_api")
        endpoint = f"/sports/{sport_key}/odds"
        params = {
            "regions": ",".join(regions or self.context.source.regions or ["uk", "eu"]),
            "markets": ",".join(markets or self.context.source.markets or ["h2h", "spreads", "totals"]),
            "oddsFormat": "decimal",
        }
        selected_bookmakers = bookmakers or self.context.source.bookmakers
        if selected_bookmakers:
            params["bookmakers"] = ",".join(selected_bookmakers)
        payload = self._get(endpoint, params)
        return map_odds_payload(payload, sport_key=sport_key)

    def sports(self, all_sports: bool = True) -> list[dict[str, Any]]:
        params = {"all": "true"} if all_sports else {}
        payload = self._get("/sports", params)
        return map_sports_payload(payload)

    def historical_odds(
        self,
        sport_key: str,
        snapshot_time: str,
        regions: list[str] | None = None,
        markets: list[str] | None = None,
        bookmakers: list[str] | None = None,
    ) -> dict[str, Any]:
        if not sport_key:
            raise DataSourceError("missing_sport_key:the_odds_api")
        if not snapshot_time:
            raise DataSourceError("missing_snapshot_time:the_odds_api")
        endpoint = f"/historical/sports/{sport_key}/odds"
        params = {
            "date": snapshot_time,
            "regions": ",".join(regions or self.context.source.regions or ["uk", "eu"]),
            "markets": ",".join(markets or self.context.source.markets or ["h2h", "spreads", "totals"]),
            "oddsFormat": "decimal",
        }
        selected_bookmakers = bookmakers or self.context.source.bookmakers
        if selected_bookmakers:
            params["bookmakers"] = ",".join(selected_bookmakers)
        payload = self._get(endpoint, params)
        return map_historical_odds_payload(payload, sport_key=sport_key)

    def _get(self, endpoint: str, params: dict[str, Any]) -> Any:
        if not self.context.api_key:
            raise DataSourceError("missing_credentials:THE_ODDS_API_KEY")
        clean_params = {key: value for key, value in params.items() if value is not None}
        clean_params["apiKey"] = self.context.api_key
        response = self.context.http.get_json(
            provider=self.provider,
            url=f"{self.context.source.base_url.rstrip('/')}{endpoint}",
            endpoint=endpoint,
            params=clean_params,
        )
        if response.error:
            raise DataSourceError(response.error)
        return response.payload


def map_sports_payload(payload: Any) -> list[dict[str, Any]]:
    rows = payload if isinstance(payload, list) else []
    sports: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        sports.append(
            {
                "key": key,
                "group": item.get("group"),
                "title": item.get("title"),
                "active": bool(item.get("active", False)),
                "has_outrights": bool(item.get("has_outrights", False)),
            }
        )
    return sports


def map_historical_odds_payload(payload: Any, sport_key: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "timestamp": None,
            "previous_timestamp": None,
            "next_timestamp": None,
            "matches": [],
            "snapshots": [],
        }
    timestamp = payload.get("timestamp")
    collected_at = _parse_datetime(str(timestamp)) if timestamp else None
    matches, snapshots = map_odds_payload(
        payload.get("data") or [],
        sport_key=sport_key,
        collected_at=collected_at,
    )
    return {
        "timestamp": timestamp,
        "previous_timestamp": payload.get("previous_timestamp"),
        "next_timestamp": payload.get("next_timestamp"),
        "matches": matches,
        "snapshots": snapshots,
    }


def map_odds_payload(
    payload: Any,
    sport_key: str,
    collected_at: datetime | None = None,
) -> tuple[list[Match], list[OddsSnapshot]]:
    rows = payload if isinstance(payload, list) else []
    matches: list[Match] = []
    snapshots: list[OddsSnapshot] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        event_id = str(item.get("id") or "")
        home = str(item.get("home_team") or "Unknown Home")
        away = str(item.get("away_team") or "Unknown Away")
        commence_time = item.get("commence_time")
        if not event_id or not commence_time:
            continue
        match_id = f"the_odds_api:{event_id}"
        matches.append(
            Match(
                id=match_id,
                league=str(item.get("sport_title") or sport_key),
                home_team=home,
                away_team=away,
                kickoff_at=_parse_datetime(str(commence_time)),
                data_completeness=0.78,
                external_ids={
                    "the_odds_api_event": event_id,
                    "the_odds_api_sport_key": sport_key,
                },
            )
        )
        snapshots.extend(
            _map_event_odds(
                item,
                match_id=match_id,
                home_team=home,
                away_team=away,
                collected_at=collected_at,
            )
        )
    return matches, snapshots


def _map_event_odds(
    event: dict[str, Any],
    match_id: str,
    home_team: str,
    away_team: str,
    collected_at: datetime | None = None,
) -> list[OddsSnapshot]:
    snapshots: list[OddsSnapshot] = []
    event_id = str(event.get("id") or "")
    for bookmaker in event.get("bookmakers", []) or []:
        if not isinstance(bookmaker, dict):
            continue
        bookmaker_key = str(bookmaker.get("key") or bookmaker.get("title") or "unknown")
        bookmaker_name = str(bookmaker.get("title") or bookmaker.get("key") or "Unknown bookmaker")
        bookmaker_updated = bookmaker.get("last_update")
        for market in bookmaker.get("markets", []) or []:
            if not isinstance(market, dict):
                continue
            market_key = str(market.get("key") or "")
            market_type = _market_type(market_key)
            if market_type is None:
                continue
            odds, line = _extract_market_odds(
                market.get("outcomes") or [],
                market_type=market_type,
                home_team=home_team,
                away_team=away_team,
            )
            if not odds:
                continue
            line_key = line or "main"
            snapshots.append(
                OddsSnapshot(
                    id=f"the_odds_api:{event_id}:{bookmaker_key}:{market_type.value}:{line_key}",
                    match_id=match_id,
                    market_type=market_type,
                    line=line,
                    source="the_odds_api",
                    bookmaker=bookmaker_name,
                    collected_at=(
                        collected_at
                        or (
                            _parse_datetime(str(market.get("last_update") or bookmaker_updated))
                            if (market.get("last_update") or bookmaker_updated)
                            else datetime.utcnow()
                        )
                    ),
                    outcome_odds=odds,
                    best_price=dict(odds),
                )
            )
    return snapshots


def sport_key_for_league(league_code: str, odds_api_slug: str | None, sport_keys: dict[str, str]) -> str | None:
    code = league_code.strip().upper()
    if code in sport_keys:
        return sport_keys[code]
    slug = (odds_api_slug or "").strip().lower()
    return _SPORT_KEYS_BY_ODDS_API_IO_SLUG.get(slug)


def _market_type(value: str) -> MarketType | None:
    lowered = value.lower()
    if lowered == "h2h":
        return MarketType.one_x_two
    if lowered == "spreads":
        return MarketType.asian_handicap
    if lowered == "totals":
        return MarketType.over_under
    return None


def _extract_market_odds(
    outcomes: Any,
    market_type: MarketType,
    home_team: str,
    away_team: str,
) -> tuple[dict[str, float], str | None]:
    rows = outcomes if isinstance(outcomes, list) else []
    odds: dict[str, float] = {}
    line: str | None = None
    for outcome in rows:
        if not isinstance(outcome, dict):
            continue
        selection = _selection(str(outcome.get("name") or ""), market_type, home_team, away_team)
        price = _safe_float(outcome.get("price"))
        point = outcome.get("point")
        if point is not None and (
            market_type != MarketType.asian_handicap or selection == "HOME" or line is None
        ):
            line = str(point)
        if selection and price:
            odds[selection] = price
    return odds, line


def _selection(value: str, market_type: MarketType, home_team: str, away_team: str) -> str | None:
    normalized = value.strip()
    lowered = normalized.lower()
    if market_type == MarketType.one_x_two:
        if normalized == home_team:
            return "HOME"
        if normalized == away_team:
            return "AWAY"
        if lowered == "draw":
            return "DRAW"
    if market_type == MarketType.asian_handicap:
        if normalized == home_team:
            return "HOME"
        if normalized == away_team:
            return "AWAY"
    if market_type == MarketType.over_under:
        if lowered == "over":
            return "OVER"
        if lowered == "under":
            return "UNDER"
    return None


def _parse_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_SPORT_KEYS_BY_ODDS_API_IO_SLUG = {
    "england-premier-league": "soccer_epl",
    "spain-la-liga": "soccer_spain_la_liga",
    "italy-serie-a": "soccer_italy_serie_a",
    "germany-bundesliga": "soccer_germany_bundesliga",
    "france-ligue-1": "soccer_france_ligue_one",
    "usa-mls": "soccer_usa_mls",
    "australia-a-league": "soccer_australia_aleague",
    "republic-of-korea-k-league-1": "soccer_korea_kleague1",
    "japan-j1-league": "soccer_japan_j_league",
    "brazil-brasileiro-serie-a": "soccer_brazil_campeonato",
    "mexico-liga-mx": "soccer_mexico_ligamx",
    "international-fifa-world-cup": "soccer_fifa_world_cup",
}
