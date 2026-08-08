# app/routers/operations.py
"""
Роутер операций: создание, получение статуса, истории.

На этом этапе реализован только POST /operations.
Остальные методы будут добавлены на следующих этапах.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_session
from app.models import Operation, OperationStatus, Event
from app.schemas import CreateOperationRequest, OperationResponse

router = APIRouter(prefix="/operations", tags=["operations"])

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
    # Коммит произойдёт в dependencies.get_session после выхода из функции.
    # Явно flush не нужен — SQLAlchemy сделает это перед коммитом.

    return OperationResponse.model_validate(operation)
