from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ScoreBreakdown(BaseModel):
    odds_edge: float = 0.0
    data_quality: float = 0.0
    history_signal: float = 0.0
    news_signal: float = 0.0
    risk_penalty: float = 0.0
    movement_penalty: float = 0.0
    final_value_score: float = 0.0
    final_risk_score: float = 0.0
    gates_failed: list[str] = Field(default_factory=list)


class SourceResponse(BaseModel):
    provider: str
    endpoint: str
    request_key: str
    status_code: int | None = None
    payload: Any = None
    cached: bool = False
    duration_ms: int = 0
    error: str | None = None


class HistoricalMatchRow(BaseModel):
    id: str
    league: str
    season: str
    date: datetime
    home_team: str
    away_team: str
    home_goals: int | None = None
    away_goals: int | None = None
    home_odds: float | None = None
    draw_odds: float | None = None
    away_odds: float | None = None
    max_home_odds: float | None = None
    max_draw_odds: float | None = None
    max_away_odds: float | None = None
    avg_home_odds: float | None = None
    avg_draw_odds: float | None = None
    avg_away_odds: float | None = None
    ah_line: float | None = None
    ah_home_odds: float | None = None
    ah_away_odds: float | None = None
    avg_ah_home_odds: float | None = None
    avg_ah_away_odds: float | None = None
    closing_ah_home_odds: float | None = None
    closing_ah_away_odds: float | None = None
    closing_home_odds: float | None = None
    closing_draw_odds: float | None = None
    closing_away_odds: float | None = None


@dataclass(frozen=True)
class RequestShape:
    provider: str
    endpoint: str
    params: dict[str, Any]
