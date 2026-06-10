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

    def __init__(self, context: ClientContext):
        self.context = context

    def download_csv(self, league: str, season: str) -> str:
        endpoint = f"/mmz4281/{season}/{league}.csv"
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


def parse_csv_text(league: str, season: str, text: str) -> list[HistoricalMatchRow]:
    rows: list[HistoricalMatchRow] = []
    reader = csv.DictReader(StringIO(text))
    for raw in reader:
        if not raw.get("Date") or not raw.get("HomeTeam") or not raw.get("AwayTeam"):
            continue
        date = _parse_date(raw["Date"])
        home = raw["HomeTeam"].strip()
        away = raw["AwayTeam"].strip()
        row_id = f"football_data_uk:{league}:{season}:{date.date()}:{home}:{away}"
        rows.append(
            HistoricalMatchRow(
                id=row_id,
                league=league,
                season=season,
                date=date,
                home_team=home,
                away_team=away,
                home_goals=_safe_int(raw.get("FTHG")),
                away_goals=_safe_int(raw.get("FTAG")),
                home_odds=_first_float(raw, ["B365H", "PSH", "MaxH", "AvgH"]),
                draw_odds=_first_float(raw, ["B365D", "PSD", "MaxD", "AvgD"]),
                away_odds=_first_float(raw, ["B365A", "PSA", "MaxA", "AvgA"]),
                max_home_odds=_first_float(raw, ["MaxH", "B365H", "PSH"]),
                max_draw_odds=_first_float(raw, ["MaxD", "B365D", "PSD"]),
                max_away_odds=_first_float(raw, ["MaxA", "B365A", "PSA"]),
                avg_home_odds=_first_float(raw, ["AvgH", "B365H", "PSH"]),
                avg_draw_odds=_first_float(raw, ["AvgD", "B365D", "PSD"]),
                avg_away_odds=_first_float(raw, ["AvgA", "B365A", "PSA"]),
                ah_line=_first_float(raw, ["AHh"]),
                ah_home_odds=_first_float(raw, ["MaxAHH", "B365AHH", "PAHH"]),
                ah_away_odds=_first_float(raw, ["MaxAHA", "B365AHA", "PAHA"]),
                avg_ah_home_odds=_first_float(raw, ["AvgAHH", "B365AHH", "PAHH"]),
                avg_ah_away_odds=_first_float(raw, ["AvgAHA", "B365AHA", "PAHA"]),
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
