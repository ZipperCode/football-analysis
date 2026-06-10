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


class SourceSettings(BaseModel):
    name: str
    enabled: bool = True
    base_url: str
    api_key_env: str | None = None
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


class BacktestSettings(BaseModel):
    data_dir: str = "data/historical"
    default_league: str = "E0"
    default_season: str = "2526"


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
