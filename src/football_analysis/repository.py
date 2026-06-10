from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, TypeVar

from pydantic import BaseModel
from sqlalchemy import JSON, DateTime, String, create_engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class Record(Base):
    __tablename__ = "records"

    bucket: Mapped[str] = mapped_column(String(80), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


ModelT = TypeVar("ModelT", bound=BaseModel)


class RecordRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url
        connect_args: dict[str, Any] = {}
        if database_url.startswith("sqlite:///"):
            db_path = database_url.removeprefix("sqlite:///")
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            connect_args["check_same_thread"] = False
        self.engine = create_engine(database_url, connect_args=connect_args, future=True)

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)

    def count(self, bucket: str) -> int:
        with Session(self.engine) as session:
            return len(session.scalars(select(Record).where(Record.bucket == bucket)).all())

    def upsert_model(self, bucket: str, record_id: str, model: BaseModel) -> None:
        self.upsert_payload(bucket, record_id, model.model_dump(mode="json"))

    def upsert_payload(self, bucket: str, record_id: str, payload: dict[str, Any]) -> None:
        with Session(self.engine) as session:
            record = session.get(Record, (bucket, record_id))
            if record is None:
                session.add(Record(bucket=bucket, record_id=record_id, payload=payload))
            else:
                record.payload = payload
                record.updated_at = datetime.utcnow()
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                record = session.get(Record, (bucket, record_id))
                if record is None:
                    raise
                record.payload = payload
                record.updated_at = datetime.utcnow()
                session.commit()

    def get_model(self, bucket: str, record_id: str, model_type: type[ModelT]) -> ModelT | None:
        with Session(self.engine) as session:
            record = session.get(Record, (bucket, record_id))
            if record is None:
                return None
            return model_type.model_validate(record.payload)

    def list_models(self, bucket: str, model_type: type[ModelT]) -> list[ModelT]:
        with Session(self.engine) as session:
            records = session.scalars(
                select(Record).where(Record.bucket == bucket).order_by(Record.record_id)
            ).all()
            return [model_type.model_validate(record.payload) for record in records]

    def replace_bucket(self, bucket: str, models: Iterable[BaseModel]) -> None:
        with Session(self.engine) as session:
            session.execute(delete(Record).where(Record.bucket == bucket))
            for model in models:
                record_id = str(getattr(model, "id"))
                session.add(Record(bucket=bucket, record_id=record_id, payload=model.model_dump(mode="json")))
            session.commit()

    def close(self) -> None:
        self.engine.dispose()
