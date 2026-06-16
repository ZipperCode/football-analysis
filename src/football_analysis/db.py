from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, TypeVar

from pydantic import BaseModel
from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, create_engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from football_analysis.contracts import HistoricalMatchRow
from football_analysis.models import AgentFinding, BetLog, JobRun, Match, OddsSnapshot, Recommendation, StrategySnapshot


class StructuredBase(DeclarativeBase):
    pass


class PayloadMixin:
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CompetitionRow(StructuredBase, PayloadMixin):
    __tablename__ = "competitions"

    id: Mapped[str] = mapped_column(String(180), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str | None] = mapped_column(String(120))
    provider: Mapped[str | None] = mapped_column(String(80))


class TeamRow(StructuredBase, PayloadMixin):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(180), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str | None] = mapped_column(String(120))
    provider: Mapped[str | None] = mapped_column(String(80))


class MatchRow(StructuredBase, PayloadMixin):
    __tablename__ = "matches"

    id: Mapped[str] = mapped_column(String(180), primary_key=True)
    league: Mapped[str] = mapped_column(String(200), nullable=False)
    home_team: Mapped[str] = mapped_column(String(200), nullable=False)
    away_team: Mapped[str] = mapped_column(String(200), nullable=False)
    kickoff_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    data_completeness: Mapped[float] = mapped_column(Float, default=0.0)


class OddsSnapshotRow(StructuredBase, PayloadMixin):
    __tablename__ = "odds_snapshots"

    id: Mapped[str] = mapped_column(String(220), primary_key=True)
    match_id: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    market_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    bookmaker: Mapped[str] = mapped_column(String(160), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AgentFindingRow(StructuredBase, PayloadMixin):
    __tablename__ = "agent_findings"

    id: Mapped[str] = mapped_column(String(220), primary_key=True)
    match_id: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String(120), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)


class RecommendationRow(StructuredBase, PayloadMixin):
    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(String(240), primary_key=True)
    match_id: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    value_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)



class StrategySnapshotRow(StructuredBase, PayloadMixin):
    __tablename__ = "strategy_snapshots"

    id: Mapped[str] = mapped_column(String(260), primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    match_id: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    strategy_name: Mapped[str] = mapped_column(String(120), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    decision_stage: Mapped[str] = mapped_column(String(80), nullable=False)
    decision_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    market_type: Mapped[str | None] = mapped_column(String(80))
    selection: Mapped[str | None] = mapped_column(String(120))
    recommendation_status: Mapped[str] = mapped_column(String(40), nullable=False)
    expected_value: Mapped[float | None] = mapped_column(Float)
    clv: Mapped[float | None] = mapped_column(Float)
    settlement_result: Mapped[str | None] = mapped_column(String(40))
    profit_units: Mapped[float | None] = mapped_column(Float)
    stake_units: Mapped[float] = mapped_column(Float, default=0.0)


class BetRow(StructuredBase, PayloadMixin):
    __tablename__ = "bets"

    id: Mapped[str] = mapped_column(String(180), primary_key=True)
    match_id: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    market_type: Mapped[str] = mapped_column(String(80), nullable=False)
    selection: Mapped[str] = mapped_column(String(80), nullable=False)
    stake_units: Mapped[float] = mapped_column(Float, default=0.0)
    profit_units: Mapped[float | None] = mapped_column(Float)


class RawPayloadRow(StructuredBase):
    __tablename__ = "raw_payloads"

    id: Mapped[str] = mapped_column(String(260), primary_key=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(String(160), nullable=False)
    request_key: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    status_code: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[Any] = mapped_column(JSON, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    cached_until: Mapped[datetime | None] = mapped_column(DateTime)


class SourceRequestRow(StructuredBase):
    __tablename__ = "source_requests"

    id: Mapped[str] = mapped_column(String(260), primary_key=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(String(160), nullable=False)
    request_key: Mapped[str] = mapped_column(String(180), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    cached: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sanitized_params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class QuotaWindowRow(StructuredBase):
    __tablename__ = "quota_windows"

    id: Mapped[str] = mapped_column(String(220), primary_key=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    window_key: Mapped[str] = mapped_column(String(80), nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class JobRunRow(StructuredBase, PayloadMixin):
    __tablename__ = "job_runs"

    id: Mapped[str] = mapped_column(String(180), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    source: Mapped[str | None] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class HistoricalMatchRowModel(StructuredBase, PayloadMixin):
    __tablename__ = "historical_matches"

    id: Mapped[str] = mapped_column(String(220), primary_key=True)
    league: Mapped[str] = mapped_column(String(80), nullable=False)
    season: Mapped[str] = mapped_column(String(20), nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    home_team: Mapped[str] = mapped_column(String(200), nullable=False)
    away_team: Mapped[str] = mapped_column(String(200), nullable=False)


ModelT = TypeVar("ModelT", bound=BaseModel)


class StructuredRepository:
    _bucket_rows: dict[str, type[StructuredBase]] = {
        "matches": MatchRow,
        "odds": OddsSnapshotRow,
        "findings": AgentFindingRow,
        "recommendations": RecommendationRow,
        "strategy_snapshots": StrategySnapshotRow,
        "bets": BetRow,
        "jobs": JobRunRow,
        "historical_matches": HistoricalMatchRowModel,
    }

    def __init__(self, database_url: str):
        self.database_url = database_url
        connect_args: dict[str, Any] = {}
        if database_url.startswith("sqlite:///"):
            db_path = database_url.removeprefix("sqlite:///")
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            connect_args["check_same_thread"] = False
        self.engine = create_engine(database_url, connect_args=connect_args, future=True)

    def initialize(self) -> None:
        StructuredBase.metadata.create_all(self.engine)

    def close(self) -> None:
        self.engine.dispose()

    def count(self, bucket: str) -> int:
        row_type = self._row_type(bucket)
        with Session(self.engine) as session:
            return len(session.scalars(select(row_type)).all())

    def upsert_model(self, bucket: str, record_id: str, model: BaseModel) -> None:
        payload = model.model_dump(mode="json")
        values = self._row_values(bucket, record_id, model, payload)
        self._upsert_row(self._row_type(bucket), record_id, values)

    def get_model(self, bucket: str, record_id: str, model_type: type[ModelT]) -> ModelT | None:
        row_type = self._row_type(bucket)
        with Session(self.engine) as session:
            row = session.get(row_type, record_id)
            if row is None:
                return None
            return model_type.model_validate(row.payload)

    def list_models(self, bucket: str, model_type: type[ModelT]) -> list[ModelT]:
        row_type = self._row_type(bucket)
        with Session(self.engine) as session:
            rows = session.scalars(select(row_type).order_by(row_type.id)).all()
            return [model_type.model_validate(row.payload) for row in rows]

    def replace_bucket(self, bucket: str, models: Iterable[BaseModel]) -> None:
        row_type = self._row_type(bucket)
        with Session(self.engine) as session:
            session.execute(delete(row_type))
            for model in models:
                record_id = str(getattr(model, "id"))
                session.add(row_type(**self._row_values(bucket, record_id, model, model.model_dump(mode="json"))))
            session.commit()

    def save_raw_payload(
        self,
        provider: str,
        endpoint: str,
        request_key: str,
        status_code: int | None,
        payload: Any,
        ttl_seconds: int | None,
    ) -> None:
        captured_at = datetime.utcnow()
        row_id = f"{provider}:{request_key}:{int(captured_at.timestamp() * 1000)}"
        cached_until = captured_at + timedelta(seconds=ttl_seconds) if ttl_seconds else None
        with Session(self.engine) as session:
            session.add(
                RawPayloadRow(
                    id=row_id,
                    provider=provider,
                    endpoint=endpoint,
                    request_key=request_key,
                    status_code=status_code,
                    payload=payload,
                    captured_at=captured_at,
                    cached_until=cached_until,
                )
            )
            session.commit()

    def get_cached_payload(self, provider: str, endpoint: str, request_key: str) -> Any | None:
        now = datetime.utcnow()
        with Session(self.engine) as session:
            rows = session.scalars(
                select(RawPayloadRow)
                .where(RawPayloadRow.provider == provider)
                .where(RawPayloadRow.endpoint == endpoint)
                .where(RawPayloadRow.request_key == request_key)
                .order_by(RawPayloadRow.captured_at.desc())
            ).all()
            for row in rows:
                if row.cached_until and row.cached_until > now:
                    return row.payload
        return None

    def record_source_request(
        self,
        provider: str,
        endpoint: str,
        request_key: str,
        status_code: int | None,
        cached: bool,
        duration_ms: int,
        sanitized_params: dict[str, Any],
        error: str | None = None,
    ) -> None:
        row_id = f"{provider}:{request_key}:{int(datetime.utcnow().timestamp() * 1000)}"
        with Session(self.engine) as session:
            session.add(
                SourceRequestRow(
                    id=row_id,
                    provider=provider,
                    endpoint=endpoint,
                    request_key=request_key,
                    status_code=status_code,
                    cached=1 if cached else 0,
                    duration_ms=duration_ms,
                    sanitized_params=sanitized_params,
                    error=error,
                )
            )
            session.commit()

    def cache_count(self, provider: str | None = None) -> int:
        with Session(self.engine) as session:
            statement = select(RawPayloadRow)
            if provider:
                statement = statement.where(RawPayloadRow.provider == provider)
            return len(session.scalars(statement).all())

    def quota_count(self, provider: str, window_key: str) -> int:
        with Session(self.engine) as session:
            row = session.get(QuotaWindowRow, f"{provider}:{window_key}")
            return row.count if row else 0

    def increment_quota(self, provider: str, window_key: str) -> int:
        row_id = f"{provider}:{window_key}"
        with Session(self.engine) as session:
            row = session.get(QuotaWindowRow, row_id)
            if row is None:
                row = QuotaWindowRow(id=row_id, provider=provider, window_key=window_key, count=0)
                session.add(row)
            row.count += 1
            row.updated_at = datetime.utcnow()
            session.commit()
            return row.count

    def quota_snapshot(self, provider: str) -> dict[str, int]:
        with Session(self.engine) as session:
            rows = session.scalars(select(QuotaWindowRow).where(QuotaWindowRow.provider == provider)).all()
            return {row.window_key: row.count for row in rows}

    def _row_type(self, bucket: str) -> type[StructuredBase]:
        try:
            return self._bucket_rows[bucket]
        except KeyError as exc:
            raise KeyError(f"Unsupported structured bucket: {bucket}") from exc

    def _upsert_row(self, row_type: type[StructuredBase], record_id: str, values: dict[str, Any]) -> None:
        with Session(self.engine) as session:
            row = session.get(row_type, record_id)
            if row is None:
                session.add(row_type(**values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
                if hasattr(row, "updated_at"):
                    row.updated_at = datetime.utcnow()
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                row = session.get(row_type, record_id)
                if row is None:
                    raise
                for key, value in values.items():
                    setattr(row, key, value)
                session.commit()

    def _row_values(self, bucket: str, record_id: str, model: BaseModel, payload: dict[str, Any]) -> dict[str, Any]:
        if isinstance(model, Match):
            return {
                "id": record_id,
                "league": model.league,
                "home_team": model.home_team,
                "away_team": model.away_team,
                "kickoff_at": model.kickoff_at,
                "status": model.status.value,
                "data_completeness": model.data_completeness,
                "payload": payload,
            }
        if isinstance(model, OddsSnapshot):
            return {
                "id": record_id,
                "match_id": model.match_id,
                "market_type": model.market_type.value,
                "source": model.source,
                "bookmaker": model.bookmaker,
                "collected_at": model.collected_at,
                "payload": payload,
            }
        if isinstance(model, AgentFinding):
            return {
                "id": record_id,
                "match_id": model.match_id,
                "agent_name": model.agent_name,
                "confidence": model.confidence,
                "payload": payload,
            }
        if isinstance(model, Recommendation):
            return {
                "id": record_id,
                "match_id": model.match_id,
                "status": model.status.value,
                "value_score": model.value_score,
                "risk_score": model.risk_score,
                "confidence": model.confidence,
                "created_at": model.created_at,
                "payload": payload,
            }
        if isinstance(model, StrategySnapshot):
            return {
                "id": record_id,
                "recommendation_id": model.recommendation_id,
                "match_id": model.match_id,
                "strategy_name": model.strategy_name,
                "strategy_version": model.strategy_version,
                "decision_stage": model.decision_stage,
                "decision_time": model.decision_time,
                "market_type": model.market_type.value if model.market_type else None,
                "selection": model.selection,
                "recommendation_status": model.recommendation_status.value,
                "expected_value": model.expected_value,
                "clv": model.clv,
                "settlement_result": model.settlement_result,
                "profit_units": model.profit_units,
                "stake_units": model.stake_units,
                "payload": payload,
            }
        if isinstance(model, BetLog):
            return {
                "id": record_id,
                "match_id": model.match_id,
                "market_type": model.market_type.value,
                "selection": model.selection,
                "stake_units": model.stake_units,
                "profit_units": model.profit_units,
                "payload": payload,
            }
        if isinstance(model, JobRun):
            return {
                "id": record_id,
                "job_type": model.job_type,
                "status": model.status.value,
                "source": model.source,
                "started_at": model.started_at,
                "finished_at": model.finished_at,
                "payload": payload,
            }
        if isinstance(model, HistoricalMatchRow):
            return {
                "id": record_id,
                "league": model.league,
                "season": model.season,
                "date": model.date,
                "home_team": model.home_team,
                "away_team": model.away_team,
                "payload": payload,
            }
        return {"id": record_id, "payload": payload}


def init_db(database_url: str) -> StructuredRepository:
    repository = StructuredRepository(database_url)
    repository.initialize()
    return repository
