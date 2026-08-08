# app/routers/operations.py
"""
Роутер операций: создание, получение статуса, истории.

На этом этапе реализованы:
  - POST /operations          (создание)
  - GET  /operations/{id}      (получение состояния)
  - GET  /operations/{id}/events (история событий)
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.dependencies import get_session
from app.models import Operation, OperationStatus, Event
from app.schemas import (
    CreateOperationRequest, OperationResponse, EventResponse
)

router = APIRouter(prefix="/operations", tags=["operations"])

# ── Вспомогательная функция ────────────────────────────────────────────────

async def _get_operation_or_404(
    operation_id: str, session: AsyncSession
) -> Operation:
    """
    Загружает операцию по operation_id или выбрасывает 404.
    """
    operation = await session.get(Operation, operation_id)
    if operation is None:
        raise HTTPException(
            status_code=404,
            detail=f"Операция с operationId='{operation_id}' не найдена",
        )
    return operation

# ── POST /operations ───────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_operation(
    body: CreateOperationRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Создание новой платёжной операции.

    - Принимает operationId, amount, currency, description.
    - Валидирует входные данные (через Pydantic).
    - Если operationId уже существует — 409 Conflict.
    - Иначе: создаёт операцию в статусе CREATED, записывает событие, возвращает 201.
    """
    # 1. Проверить, не существует ли уже операция с таким operationId.
    existing = await session.get(Operation, body.operation_id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Операция с operationId='{body.operation_id}' уже существует",
        )

    # 2. Текущее время в UTC — единое для всех записей в рамках этой транзакции.
    now = datetime.now(timezone.utc)

    # 3. Создать объект Operation в статусе CREATED.
    operation = Operation(
        operation_id=body.operation_id,
        amount=body.amount,
        currency=body.currency,
        description=body.description,
        status=OperationStatus.CREATED,
        provider_payment_id=None,
        created_at=now,
        updated_at=now,
    )
    session.add(operation)

    # 4. Создать первое событие CREATED.
    event = Event(
        operation_id=body.operation_id,
        event_id=1,  # Первое событие всегда имеет номер 1
        type="CREATED",
        from_status=None,
        to_status=OperationStatus.CREATED,
        message="Operation created",
        occurred_at=now,
    )
    session.add(event)

    # 5. Коммит и ответ.
    return OperationResponse.model_validate(operation)

# ── GET /operations/{id} ───────────────────────────────────────────────────

@router.get("/{operation_id}")
async def get_operation(
    operation_id: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Получить текущее состояние операции.
    Возвращает operationId, amount, currency, description, status, providerPaymentId.
    Если операция не найдена — 404.
    """
    operation = await _get_operation_or_404(operation_id, session)
    return OperationResponse.model_validate(operation)

# ── GET /operations/{id}/events ────────────────────────────────────────────

@router.get("/{operation_id}/events")
async def get_operation_events(
    operation_id: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Получить историю переходов операции.

    Возвращает массив событий в порядке event_id (монотонно возрастает).
    Если операция не найдена — 404.
    Если операция существует, но событий нет — пустой массив ([]).
    """
    # Загружаем операцию вместе с событиями
    stmt = (
        select(Operation)
        .where(Operation.operation_id == operation_id)
        .options(selectinload(Operation.events))
    )
    result = await session.execute(stmt)
    operation = result.scalar_one_or_none()

    if operation is None:
        raise HTTPException(
            status_code=404,
            detail=f"Операция с operationId='{operation_id}' не найдена",
        )

    # События загружены и отсортированы по event_id на уровне relationship
    return [EventResponse.model_validate(event) for event in operation.events]
