# app/models.py

"""
SQLAlchemy-модели для таблиц PostgreSQL.

Три таблицы:
  - operations      — платёжные операции
  - submit_intents  — намерения отправки (outbox)
  - events          — история переходов между статусами
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Integer, DateTime, ForeignKey, UniqueConstraint,
    Index, Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Базовый класс для всех моделей."""
    pass


class OperationStatus:
    # Перечисление статусов (хранится как строка, валидация на уровне кода)
    CREATED: str = "CREATED"
    PROCESSING: str = "PROCESSING"
    COMPLETED: str = "COMPLETED"
    REJECTED: str = "REJECTED"

    ALL: set[str] = {CREATED, PROCESSING, COMPLETED, REJECTED}
    FINAL: set[str] = {COMPLETED, REJECTED}


class Operation(Base):
    __tablename__ = "operations"

    # operation_id — внешний строковый идентификатор, переданный клиентом
    operation_id = Column(String(128), primary_key=True)

    amount = Column(String(20), nullable=False)
    currency = Column(String(3), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default=OperationStatus.CREATED)

    # provider_payment_id — заполняется либо из ответа провайдера,
    # либо из первой пришедшей callback-квитанции
    provider_payment_id = Column(String(64), nullable=True, default=None)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Связь с событиями: одна операция → много событий
    events = relationship(
        "Event",
        back_populates="operation",
        order_by="Event.event_id",
        lazy="selectin",
    )

    def to_dict(self) -> dict:
        """Сериализация в словарь для JSON-ответа API."""
        return {
            "operationId": self.operation_id,
            "amount": self.amount,
            "currency": self.currency,
            "description": self.description,
            "status": self.status,
            "providerPaymentId": self.provider_payment_id,
        }


class SubmitIntent(Base):
    __tablename__ = "submit_intents"

    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    operation_id = Column(
        String(128),
        ForeignKey("operations.operation_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # Гарантия: не более одного намерения на операцию
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class Event(Base):
    __tablename__ = "events"

    # Составной первичный ключ: операция + монотонный номер внутри операции
    operation_id = Column(
        String(128),
        ForeignKey("operations.operation_id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_id = Column(Integer, primary_key=True)

    type = Column(String(40), nullable=False)
    from_status = Column(String(20), nullable=True)
    to_status = Column(String(20), nullable=False)
    message = Column(Text, nullable=True)
    occurred_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Обратная связь к операции
    operation = relationship("Operation", back_populates="events")

    __table_args__ = (
        # Индекс для быстрого получения всех событий операции
        Index("ix_events_operation_id", "operation_id"),
    )

    def to_dict(self) -> dict:
        """Сериализация в словарь для JSON-ответа API."""
        return {
            "eventId": self.event_id,
            "type": self.type,
            "fromStatus": self.from_status,
            "toStatus": self.to_status,
            "message": self.message,
            "occurredAt": self.occurred_at.isoformat(),
        }
