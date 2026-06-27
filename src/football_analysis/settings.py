from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class StorageSettings(BaseModel):
    database_url: str = "sqlite:///./data/football_analysis.db"


class ThresholdSettings(BaseModel):
    min_data_quality: float = 0.70
    min_value_score: float = 62.0
    max_risk_score: float = 55.0
    max_stake_units: float = 1.5


class LiveTradingSettings(BaseModel):
    enabled: bool = True
    min_bookmakers: int = Field(default=2, ge=1)
    max_odds_age_minutes: int = Field(default=90, ge=1)
    max_execution_odds_slippage: float = Field(default=0.01, ge=0.0, le=1.0)
    min_data_quality: float = Field(default=0.82, ge=0.0, le=1.0)
    min_value_score: float = Field(default=68.0, ge=0.0, le=100.0)
    max_risk_score: float = Field(default=42.0, ge=0.0, le=100.0)
    min_confidence: float = Field(default=0.58, ge=0.0, le=1.0)
    min_edge: float = Field(default=0.025, ge=0.0)
    min_recommendation_odds: float = Field(default=1.20, ge=1.0)
    max_recommendation_odds: float = Field(default=3.25, ge=1.0)
    min_long_horizon_bets: int = Field(default=150, ge=1)
    min_long_horizon_roi: float = 0.08
    min_holdout_bets: int = Field(default=80, ge=1)
    min_holdout_roi: float = 0.08
    min_holdout_positive_rate: float = Field(default=0.60, ge=0.0, le=1.0)
    min_average_clv: float = 0.01
    max_worst_season_roi: float = -0.35
    max_recent_consecutive_losses: int = Field(default=3, ge=1)
    rolling_window_settled_bets: int = Field(default=8, ge=1)
    min_rolling_settled_bets: int = Field(default=5, ge=1)
    max_rolling_loss_units: float = Field(default=2.0, ge=0.0)
    min_rolling_roi: float = -0.25
    review_min_settled_bets: int = Field(default=6, ge=1)
    review_min_roi: float = 0.0
    review_min_average_clv: float = 0.0
    review_pause_roi: float = -0.15
    max_stake_units_per_pick: float = Field(default=0.5, ge=0.0)
    max_daily_stake_units: float = Field(default=1.2, ge=0.0)


class TierPolicySettings(BaseModel):
    # Tier policies tighten or cap live recommendations after the base score is calculated.
    label: str = "live_scoring"
    min_data_quality: float | None = Field(default=None, ge=0.0, le=1.0)
    min_value_score: float | None = Field(default=None, ge=0.0, le=100.0)
    max_risk_score: float | None = Field(default=None, ge=0.0, le=100.0)
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    max_stake_units: float | None = Field(default=None, ge=0.0)
    min_bookmakers: int | None = Field(default=None, ge=1)


class SourceSettings(BaseModel):
    name: str
    enabled: bool = True
    base_url: str
    api_key_env: str | None = None
    bookmakers: list[str] = Field(default_factory=list)
    free_tier_note: str | None = None


class IngestionSettings(BaseModel):
    request_timeout_seconds: float = 12.0
    max_retries: int = 2
    default_lookahead_days: int = 2
    default_lookback_days: int = 0
    store_raw_payloads: bool = True


class CacheSettings(BaseModel):
    enabled: bool = True
    default_ttl_seconds: int = 10800
    odds_ttl_seconds: int = 10800
    fixtures_ttl_seconds: int = 21600
    historical_ttl_seconds: int = 604800


class SourceQuotaSettings(BaseModel):
    requests_per_minute: int | None = None
    requests_per_hour: int | None = None
    requests_per_day: int | None = None


class QuotaSettings(BaseModel):
    providers: dict[str, SourceQuotaSettings] = Field(default_factory=dict)


class LeagueSettings(BaseModel):
    code: str
    name: str
    country: str | None = None
    season: int | None = None
    api_football_league_id: int | None = None
    odds_api_slug: str | None = None
    football_data_org_code: str | None = None
    football_data_uk_code: str | None = None
    # League tier fields keep production picks separate from paper strategy incubation.
    aliases: list[str] = Field(default_factory=list)
    tier: str = Field(default="secondary_professional")
    analysis_depth: str = Field(default="standard")
    strategy_mode: str = Field(default="paper")
    min_bookmakers: int = Field(default=2, ge=1)
    max_events: int | None = Field(default=20, ge=1)
    paper_only: bool = True


class BacktestSettings(BaseModel):
    data_dir: str = "data/historical"
    default_league: str = "E0"
    default_season: str = "2526"


class StrategyProfileSettings(BaseModel):
    id: str
    name: str
    league_code: str
    market_type: str
    selections: list[str] = Field(default_factory=list)
    season_phases: list[str] = Field(default_factory=lambda: ["all"])
    stability_label: str
    roi: float | None = None
    settled_bets: int = 0
    positive_folds: int = 0
    fold_count: int = 0
    average_clv: float | None = None
    active: bool = True
    live_enabled: bool = False
    max_stake_units: float | None = Field(default=None, ge=0.0)
    long_horizon_roi: float | None = None
    long_horizon_settled_bets: int = 0
    holdout_roi: float | None = None
    holdout_settled_bets: int = 0
    holdout_positive_seasons: int = 0
    holdout_season_count: int = 0
    worst_season_roi: float | None = None


class TelegramSettings(BaseModel):
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    chat_id_env: str = "TELEGRAM_CHAT_ID"


class AppSettings(BaseModel):
    name: str = "football-analysis"
    timezone: str = "Asia/Shanghai"
    fixture_mode: bool = True
    daily_pick_limit: int = 5
    markets: list[str] = Field(default_factory=lambda: ["1x2", "asian_handicap", "over_under"])
    risk_notice: str = "仅供赛前分析，不保证收益；请自行承担风险，并严格控制最大仓位。"

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class Settings(BaseModel):
    app: AppSettings = Field(default_factory=AppSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    quota: QuotaSettings = Field(default_factory=QuotaSettings)
    leagues: list[LeagueSettings] = Field(default_factory=list)
    backtest: BacktestSettings = Field(default_factory=BacktestSettings)
    strategy_profiles: list[StrategyProfileSettings] = Field(default_factory=list)
    live_trading: LiveTradingSettings = Field(default_factory=LiveTradingSettings)
    tier_policies: dict[str, TierPolicySettings] = Field(default_factory=dict)
    thresholds: ThresholdSettings = Field(default_factory=ThresholdSettings)
    data_sources: dict[str, SourceSettings] = Field(default_factory=dict)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Config file must contain a mapping: {path}")
    return loaded


def load_settings(config_path: str | Path | None = None) -> Settings:
    load_dotenv(override=False)
    path = Path(config_path or os.getenv("FOOTBALL_CONFIG", "config/default.yaml"))
    raw = _load_yaml(path)
    settings = Settings.model_validate(raw)

    if database_url := os.getenv("DATABASE_URL"):
        settings.storage.database_url = database_url

    return settings


def remote_validation_enabled() -> bool:
    return os.getenv("FOOTBALL_VALIDATE_REMOTE", "0").strip().lower() in {"1", "true", "yes"}
