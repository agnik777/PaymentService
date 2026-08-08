# app/schemas.py
"""
Pydantic-схемы для запросов и ответов API.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# ── Запрос на создание операции ────────────────────────────────────────────

class CreateOperationRequest(BaseModel):
    """Тело запроса POST /operations."""

    operation_id: str = Field(
        ...,
        alias="operationId",
        min_length=1,
        max_length=128,
        description="Уникальный строковый идентификатор операции, заданный клиентом",
    )
    amount: str = Field(
        ...,
        description="Положительная десятичная строка, не более 2 знаков после точки",
    )
    currency: str = Field(
        ...,
        min_length=3,
        max_length=3,
        description="Трёхбуквенный код валюты (поддерживается RUB)",
    )
    description: Optional[str] = Field(
        None,
        max_length=512,
        description="Произвольное описание операции",
    )

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: str) -> str:
        """
        Валидация amount: положительное число, не более 2 знаков после точки.

        Допустимые примеры: "1000.00", "0.01", "9999999.99"
        Недопустимые:       "-1.00", "0.001", "abc", "0.00", "0"
        """
        # Проверяем формат: цифры, опционально точка + 1-2 цифры
        if not re.fullmatch(r"\d+(\.\d{1,2})?", value):
            raise ValueError(
                "amount должен быть положительным числом с не более чем двумя знаками после точки"
            )

        # Проверяем, что число больше нуля
        numeric = float(value)
        if numeric <= 0:
            raise ValueError("amount должен быть положительным (> 0)")
        return value

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        """Поддерживается только RUB."""
        upper = value.upper()
        if upper != "RUB":
            raise ValueError("Поддерживается только валюта RUB")
        return upper

    model_config = {
        "populate_by_name": True,
    }

# ── Ответ операции ─────────────────────────────────────────────────────────

class OperationResponse(BaseModel):
    """Тело ответа для GET /operations/{id} и POST /operations."""

    operation_id: str = Field(..., alias="operationId")
    amount: str
    currency: str
    description: Optional[str] = None
    status: str
    provider_payment_id: Optional[str] = Field(None, alias="providerPaymentId")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")

    model_config = {
        "populate_by_name": True,
        "from_attributes": True,
    }

# ── Ответ на submit ────────────────────────────────────────────────────────

class SubmitResponse(BaseModel):
    """
    Тело ответа для POST /operations/{id}/submit.

    Содержит все поля OperationResponse, но выделено в отдельную схему,
    потому что семантика разная: submit возвращает либо 202 (принято
    в обработку), либо 200 (уже было обработано ранее).
    """

    operation_id: str = Field(..., alias="operationId")
    amount: str
    currency: str
    description: Optional[str] = None
    status: str
    provider_payment_id: Optional[str] = Field(None, alias="providerPaymentId")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")

    model_config = {
        "populate_by_name": True,
        "from_attributes": True,
    }

# ── Ответ по событию ───────────────────────────────────────────────────────

class EventResponse(BaseModel):
    """Одно событие из истории переходов."""

    event_id: int = Field(..., alias="eventId")
    type: str
    from_status: Optional[str] = Field(None, alias="fromStatus")
    to_status: str = Field(..., alias="toStatus")
    message: Optional[str] = None
    occurred_at: datetime = Field(..., alias="occurredAt")

    model_config = {
        "populate_by_name": True,
        "from_attributes": True,
    }

# ── Ответ с ошибкой ────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    """Стандартное тело ошибки."""

    detail: str
    status_code: int = Field(..., alias="statusCode")

    model_config = {"populate_by_name": True}
