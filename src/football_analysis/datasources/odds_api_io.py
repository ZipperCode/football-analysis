from __future__ import annotations

from datetime import datetime
from typing import Any

from football_analysis.datasources.base import ClientContext, DataSourceError
from football_analysis.models import Match, MarketType, OddsSnapshot


class OddsApiIoClient:
    provider = "odds_api_io"

    def __init__(self, context: ClientContext):
        self.context = context

    def events(self, sport: str = "football", league: str | None = None) -> list[Match]:
        try:
            payload = self._get("/events", {"sport": sport, "league": league})
        except DataSourceError as exc:
            if str(exc) == "HTTP 404":
                return []
            raise
        return map_events(payload)

    def odds(self, event_id: str, sport: str = "football", bookmakers: str | None = None) -> list[OddsSnapshot]:
        if not event_id:
            raise DataSourceError("missing_event_id:odds_api_io")
        bookmaker_param = bookmakers or _configured_bookmakers(self.context.source.bookmakers)
        params = {"sport": sport, "eventId": event_id, "bookmakers": bookmaker_param}
        payload = self._get("/odds", params)
        return map_odds(payload)

    def odds_multi(
        self,
        event_ids: list[str],
        sport: str = "football",
        bookmakers: str | None = None,
    ) -> list[OddsSnapshot]:
        clean_event_ids = [event_id.strip() for event_id in event_ids if event_id.strip()]
        if not clean_event_ids:
            return []
        bookmaker_param = bookmakers or _configured_bookmakers(self.context.source.bookmakers)
        params = {
            "sport": sport,
            "eventIds": ",".join(clean_event_ids),
            "bookmakers": bookmaker_param,
        }
        payload = self._get("/odds/multi", params)
        return map_odds(payload)

    def _get(self, endpoint: str, params: dict[str, Any]) -> Any:
        if not self.context.api_key:
            raise DataSourceError("missing_credentials:ODDS_API_IO_KEY")
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


def map_events(payload: Any) -> list[Match]:
    rows = _payload_rows(payload)
    matches: list[Match] = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        event_id = str(item.get("id") or item.get("eventId") or "")
        if not event_id:
            continue
        home = item.get("home") or item.get("homeTeam") or item.get("home_team") or "Unknown Home"
        away = item.get("away") or item.get("awayTeam") or item.get("away_team") or "Unknown Away"
        starts = item.get("startsAt") or item.get("commence_time") or item.get("startTime") or item.get("date")
        if not starts:
            continue
        league = item.get("league") or item.get("competition") or "Unknown"
        if isinstance(league, dict):
            league = league.get("name") or league.get("id") or "Unknown"
        matches.append(
            Match(
                id=f"odds_api_io:{event_id}",
                league=str(league),
                home_team=str(home),
                away_team=str(away),
                kickoff_at=_parse_datetime(str(starts)),
                data_completeness=0.75,
                external_ids={"odds_api_io_event": event_id},
            )
        )
    return matches


def _configured_bookmakers(bookmakers: list[str]) -> str:
    cleaned = [item.strip() for item in bookmakers if item.strip()]
    return ",".join(cleaned) if cleaned else "Bet365,1xbet"


def map_odds(payload: Any) -> list[OddsSnapshot]:
    rows = _payload_rows(payload)
    snapshots: list[OddsSnapshot] = []
    for event in rows or []:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id") or event.get("eventId") or "")
        bookmakers = event.get("bookmakers") or event.get("odds") or []
        if isinstance(bookmakers, dict):
            bookmakers = [{"name": name, "markets": markets} for name, markets in bookmakers.items()]
        for bookmaker in bookmakers or []:
            if not isinstance(bookmaker, dict):
                continue
            bookmaker_name = str(bookmaker.get("name") or bookmaker.get("title") or bookmaker.get("key") or "Unknown bookmaker")
            markets = bookmaker.get("markets") or bookmaker.get("bets") or []
            if isinstance(markets, dict):
                markets = [{"key": key, "outcomes": value} for key, value in markets.items()]
            for market in markets or []:
                market_key = str(market.get("key") or market.get("name") or "")
                market_type = _market_type(market_key)
                if market_type is None:
                    continue
                odds, line = _extract_market_odds(
                    market.get("outcomes") or market.get("values") or market.get("odds") or [],
                    market_type=market_type,
                )
                if odds:
                    line_key = line or "main"
                    snapshots.append(
                        OddsSnapshot(
                            id=f"odds_api_io:{event_id}:{bookmaker_name}:{market_type.value}:{line_key}",
                            match_id=f"odds_api_io:{event_id}",
                            market_type=market_type,
                            line=line,
                            source="odds_api_io",
                            bookmaker=bookmaker_name,
                            collected_at=datetime.utcnow(),
                            outcome_odds=odds,
                            best_price=dict(odds),
                        )
                    )
    return snapshots


def _market_type(value: str) -> MarketType | None:
    lowered = value.lower()
    if lowered in {"h2h", "1x2", "ml", "moneyline"} or "match" in lowered:
        return MarketType.one_x_two
    if "spread" in lowered or "handicap" in lowered:
        return MarketType.asian_handicap
    if "total" in lowered or "over" in lowered:
        return MarketType.over_under
    return None


def _payload_rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    if payload.get("id") or payload.get("eventId"):
        return [payload]
    return []


def _extract_market_odds(outcomes: Any, market_type: MarketType) -> tuple[dict[str, float], str | None]:
    rows = outcomes if isinstance(outcomes, list) else [outcomes]
    odds: dict[str, float] = {}
    line: str | None = None
    for outcome in rows:
        if not isinstance(outcome, dict):
            continue
        line = line or _line_value(outcome)
        _add_named_outcome(odds, outcome)
        _add_compact_outcomes(odds, outcome, market_type)
    return odds, line


def _add_named_outcome(odds: dict[str, float], outcome: dict[str, Any]) -> None:
    selection = _selection(str(outcome.get("name") or outcome.get("value") or ""))
    price = _safe_float(outcome.get("price") or outcome.get("odd") or outcome.get("odds"))
    if selection and price:
        odds[selection] = price


def _add_compact_outcomes(odds: dict[str, float], outcome: dict[str, Any], market_type: MarketType) -> None:
    mapping = {"home": "HOME", "away": "AWAY", "draw": "DRAW"}
    if market_type == MarketType.over_under:
        mapping = {"over": "OVER", "under": "UNDER"}
    for key, selection in mapping.items():
        price = _safe_float(outcome.get(key))
        if price:
            odds[selection] = price


def _line_value(outcome: dict[str, Any]) -> str | None:
    value = outcome.get("point") or outcome.get("hdp") or outcome.get("handicap") or outcome.get("total")
    return str(value) if value is not None else None


def _selection(value: str) -> str:
    lowered = value.lower().strip()
    if lowered in {"home", "home team"}:
        return "HOME"
    if lowered in {"draw", "tie"}:
        return "DRAW"
    if lowered in {"away", "away team"}:
        return "AWAY"
    return value.upper().replace(" ", "_")


def _parse_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
