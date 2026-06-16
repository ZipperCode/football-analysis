from __future__ import annotations

import csv
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any

from football_analysis.contracts import HistoricalMatchRow
from football_analysis.datasources.base import ClientContext, DataSourceError


class FootballDataUkClient:
    provider = "football_data_uk"
    extra_league_codes = {"BRA", "JPN", "USA"}

    def __init__(self, context: ClientContext):
        self.context = context

    def download_csv(self, league: str, season: str) -> str:
        endpoint = _csv_endpoint(league, season)
        response = self.context.http.get_text(
            provider=self.provider,
            url=f"{self.context.source.base_url.rstrip('/')}{endpoint}",
            endpoint=endpoint,
        )
        if response.error:
            raise DataSourceError(response.error)
        if not isinstance(response.payload, str):
            raise DataSourceError("invalid_payload:expected_text")
        return response.payload

    def parse_csv_text(self, league: str, season: str, text: str) -> list[HistoricalMatchRow]:
        return parse_csv_text(league, season, text)

    def parse_csv_file(self, league: str, season: str, path: Path) -> list[HistoricalMatchRow]:
        return parse_csv_text(league, season, path.read_text(encoding="utf-8-sig"))


def _csv_endpoint(league: str, season: str) -> str:
    code = league.upper()
    if code in FootballDataUkClient.extra_league_codes:
        return f"/new/{code}.csv"
    return f"/mmz4281/{season}/{code}.csv"


def parse_csv_text(league: str, season: str, text: str) -> list[HistoricalMatchRow]:
    rows: list[HistoricalMatchRow] = []
    reader = csv.DictReader(StringIO(text))
    for raw in reader:
        home = _first_text(raw, ["HomeTeam", "Home"])
        away = _first_text(raw, ["AwayTeam", "Away"])
        if not raw.get("Date") or not home or not away:
            continue
        date = _parse_date(raw["Date"])
        row_season = _first_text(raw, ["Season"]) or season
        row_id = f"football_data_uk:{league}:{row_season}:{date.date()}:{home}:{away}"
        rows.append(
            HistoricalMatchRow(
                id=row_id,
                league=league,
                season=row_season,
                date=date,
                home_team=home,
                away_team=away,
                home_goals=_safe_int(_first_text(raw, ["FTHG", "HG"])),
                away_goals=_safe_int(_first_text(raw, ["FTAG", "AG"])),
                home_odds=_first_float(raw, ["B365H", "PSH", "PSCH", "MaxH", "MaxCH", "AvgH", "AvgCH"]),
                draw_odds=_first_float(raw, ["B365D", "PSD", "PSCD", "MaxD", "MaxCD", "AvgD", "AvgCD"]),
                away_odds=_first_float(raw, ["B365A", "PSA", "PSCA", "MaxA", "MaxCA", "AvgA", "AvgCA"]),
                max_home_odds=_first_float(raw, ["MaxH", "MaxCH", "B365H", "B365CH", "PSH", "PSCH"]),
                max_draw_odds=_first_float(raw, ["MaxD", "MaxCD", "B365D", "B365CD", "PSD", "PSCD"]),
                max_away_odds=_first_float(raw, ["MaxA", "MaxCA", "B365A", "B365CA", "PSA", "PSCA"]),
                avg_home_odds=_first_float(raw, ["AvgH", "AvgCH", "B365H", "B365CH", "PSH", "PSCH"]),
                avg_draw_odds=_first_float(raw, ["AvgD", "AvgCD", "B365D", "B365CD", "PSD", "PSCD"]),
                avg_away_odds=_first_float(raw, ["AvgA", "AvgCA", "B365A", "B365CA", "PSA", "PSCA"]),
                ah_line=_first_float(raw, ["AHh", "BbAHh"]),
                ah_home_odds=_first_float(raw, ["MaxAHH", "BbMxAHH", "B365AHH", "PAHH"]),
                ah_away_odds=_first_float(raw, ["MaxAHA", "BbMxAHA", "B365AHA", "PAHA"]),
                avg_ah_home_odds=_first_float(raw, ["AvgAHH", "BbAvAHH", "B365AHH", "PAHH"]),
                avg_ah_away_odds=_first_float(raw, ["AvgAHA", "BbAvAHA", "B365AHA", "PAHA"]),
                closing_ah_home_odds=_first_float(raw, ["AvgCAHH", "MaxCAHH", "B365CAHH", "PCAHH"]),
                closing_ah_away_odds=_first_float(raw, ["AvgCAHA", "MaxCAHA", "B365CAHA", "PCAHA"]),
                closing_home_odds=_first_float(raw, ["PSCH", "AvgCH", "MaxCH"]),
                closing_draw_odds=_first_float(raw, ["PSCD", "AvgCD", "MaxCD"]),
                closing_away_odds=_first_float(raw, ["PSCA", "AvgCA", "MaxCA"]),
            )
        )
    return rows


def _parse_date(value: str) -> datetime:
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            pass
    raise DataSourceError(f"invalid_date:{value}")


def _first_text(raw: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = raw.get(key)
        if value not in {None, ""}:
            return str(value).strip()
    return None


def _safe_int(value: Any) -> int | None:
    try:
        if value in {None, ""}:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_float(raw: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = raw.get(key)
        try:
            if value not in {None, ""}:
                return float(value)
        except (TypeError, ValueError):
            continue
    return None
