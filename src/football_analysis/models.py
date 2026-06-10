from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MarketType(str, Enum):
    one_x_two = "1x2"
    asian_handicap = "asian_handicap"
    over_under = "over_under"


class MatchStatus(str, Enum):
    scheduled = "scheduled"
    postponed = "postponed"
    finished = "finished"


class RecommendationStatus(str, Enum):
    recommended = "recommended"
    analysis_only = "analysis_only"
    rejected = "rejected"


class SourceState(str, Enum):
    ok = "ok"
    disabled = "disabled"
    missing_credentials = "missing_credentials"
    remote_check_skipped = "remote_check_skipped"
    error = "error"


class AppModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EvidenceSource(AppModel):
    title: str
    url: str | None = None
    publisher: str | None = None
    observed_at: datetime = Field(default_factory=datetime.utcnow)


class Match(AppModel):
    id: str
    league: str
    home_team: str
    away_team: str
    kickoff_at: datetime
    status: MatchStatus = MatchStatus.scheduled
    data_completeness: float = Field(default=0.0, ge=0.0, le=1.0)
    season: int | None = None
    country: str | None = None
    external_ids: dict[str, str] = Field(default_factory=dict)


class OddsSnapshot(AppModel):
    id: str
    match_id: str
    market_type: MarketType
    line: str | None = None
    source: str
    bookmaker: str
    collected_at: datetime = Field(default_factory=datetime.utcnow)
    outcome_odds: dict[str, float]
    market_average: dict[str, float] = Field(default_factory=dict)
    best_price: dict[str, float] = Field(default_factory=dict)
    movement: float = 0.0


class AgentFinding(AppModel):
    id: str
    match_id: str
    agent_name: str
    summary: str
    evidence_sources: list[EvidenceSource] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    risk_tags: list[str] = Field(default_factory=list)
    score_delta: float = 0.0
    payload: dict[str, Any] = Field(default_factory=dict)


class Recommendation(AppModel):
    id: str
    match_id: str
    market_type: MarketType | None = None
    selection: str | None = None
    status: RecommendationStatus
    value_score: float = Field(ge=0.0, le=100.0)
    risk_score: float = Field(ge=0.0, le=100.0)
    confidence: float = Field(ge=0.0, le=1.0)
    stake_units: float = Field(default=0.0, ge=0.0)
    odds_basis: dict[str, Any] = Field(default_factory=dict)
    score_breakdown: dict[str, Any] = Field(default_factory=dict)
    risk_tags: list[str] = Field(default_factory=list)
    reason: str
    risk_notice: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    version: str = "v1"


class BetLog(AppModel):
    id: str
    match_id: str
    market_type: MarketType
    selection: str
    odds: float
    stake_units: float
    platform: str
    placed_at: datetime = Field(default_factory=datetime.utcnow)
    notes: str | None = None
    closing_odds: float | None = None
    result: str | None = None
    profit_units: float | None = None


class MatchAnalysis(AppModel):
    match: Match
    odds_snapshots: list[OddsSnapshot]
    findings: list[AgentFinding]
    recommendation: Recommendation


class PickList(AppModel):
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    picks: list[Recommendation]
    analyses: list[MatchAnalysis] = Field(default_factory=list)
    message: str


class PerformanceSummary(AppModel):
    bets: int
    settled_bets: int
    total_stake_units: float
    profit_units: float
    roi: float | None
    average_clv: float | None


class JobStatus(str, Enum):
    started = "started"
    succeeded = "succeeded"
    partial = "partial"
    failed = "failed"


class JobRun(AppModel):
    id: str
    job_type: str
    status: JobStatus
    source: str | None = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class IngestionResult(AppModel):
    job: JobRun
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)


class BacktestSummary(AppModel):
    league: str
    season: str
    matches: int
    bets: int
    settled_bets: int
    total_stake_units: float
    profit_units: float
    roi: float | None
    average_clv: float | None


class SourceHealth(AppModel):
    source_id: str
    name: str
    state: SourceState
    enabled: bool
    credential_present: bool
    checked_at: datetime = Field(default_factory=datetime.utcnow)
    detail: str
    quota: dict[str, Any] = Field(default_factory=dict)
    cache: dict[str, Any] = Field(default_factory=dict)
