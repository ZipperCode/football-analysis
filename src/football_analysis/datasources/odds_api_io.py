from __future__ import annotations

from datetime import datetime
from typing import Any

from football_analysis.datasources.base import ClientContext, DataSourceError
from football_analysis.models import Match, MarketType, OddsSnapshot


class OddsApiIoClient:
    provider = "odds_api_io"

    def __init__(self, context: ClientContext):
        self.context = context

    def events(self, sport: str = "soccer") -> list[Match]:
        payload = self._get("/events", {"sport": sport})
        return map_events(payload)

    def odds(self, event_id: str | None = None, sport: str = "soccer") -> list[OddsSnapshot]:
        params = {"sport": sport, "eventId": event_id}
        payload = self._get("/odds", params)
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
    rows = payload.get("data") if isinstance(payload, dict) else payload
    matches: list[Match] = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        event_id = str(item.get("id") or item.get("eventId") or "")
        if not event_id:
            continue
        home = item.get("home") or item.get("homeTeam") or item.get("home_team") or "Unknown Home"
        away = item.get("away") or item.get("awayTeam") or item.get("away_team") or "Unknown Away"
        starts = item.get("startsAt") or item.get("commence_time") or item.get("startTime")
        if not starts:
            continue
        matches.append(
            Match(
                id=f"odds_api_io:{event_id}",
                league=str(item.get("league") or item.get("competition") or "Unknown"),
                home_team=str(home),
                away_team=str(away),
                kickoff_at=_parse_datetime(str(starts)),
                data_completeness=0.56,
                external_ids={"odds_api_io_event": event_id},
            )
        )
    return matches


def map_odds(payload: Any) -> list[OddsSnapshot]:
    rows = payload.get("data") if isinstance(payload, dict) else payload
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
                outcomes = market.get("outcomes") or market.get("values") or []
                odds: dict[str, float] = {}
                for outcome in outcomes:
                    if not isinstance(outcome, dict):
                        continue
                    selection = _selection(str(outcome.get("name") or outcome.get("value") or ""))
                    price = _safe_float(outcome.get("price") or outcome.get("odd") or outcome.get("odds"))
                    if selection and price:
                        odds[selection] = price
                if odds:
                    snapshots.append(
                        OddsSnapshot(
                            id=f"odds_api_io:{event_id}:{bookmaker_name}:{market_type.value}",
                            match_id=f"odds_api_io:{event_id}",
                            market_type=market_type,
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
    if lowered in {"h2h", "1x2"} or "match" in lowered:
        return MarketType.one_x_two
    if "spread" in lowered or "handicap" in lowered:
        return MarketType.asian_handicap
    if "total" in lowered or "over" in lowered:
        return MarketType.over_under
    return None


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
