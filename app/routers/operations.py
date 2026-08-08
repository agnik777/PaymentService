# app/routers/operations.py
"""
Роутер операций: создание, получение статуса, истории.

На этом этапе реализованы:
  - POST /operations                (создание)
  - GET  /operations/{id}           (получение состояния)
  - GET  /operations/{id}/events    (история событий)
  - POST /operations/{id}/submit    (отправка провайдеру)
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, func
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.dependencies import get_session
from app.models import Operation, OperationStatus, Event, SubmitIntent
from app.schemas import (
    CreateOperationRequest, OperationResponse, SubmitResponse, EventResponse
)
from app.provider_client import ProviderClient, PaymentResult

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

async def _get_next_event_id(
    session: AsyncSession, operation_id: str
) -> int:
    """
    Вычисляет следующий event_id для операции.

    Использует SELECT COALESCE(MAX(event_id), 0) + 1.
    Работает атомарно в рамках транзакции — конкурентные вставки
    с тем же MAX приведут к IntegrityError на составном PK, и одна
    из транзакций будет откачена (что корректно, так как в рамках
    одной операции конкурентных событий быть не должно).
    """
    stmt = select(func.coalesce(func.max(Event.event_id), 0)).where(
        Event.operation_id == operation_id
    )
    result = await session.execute(stmt)
    max_id: int = result.scalar_one()
    return max_id + 1

async def _add_event(
    session: AsyncSession,
    operation_id: str,
    event_type: str,
    from_status: str | None,
    to_status: str,
    message: str | None = None,
) -> Event:
    """
    Создать событие с монотонно возрастающим event_id.

    Вызывать ТОЛЬКО внутри транзакции, где уже есть блокировка
    строки операции (SELECT ... FOR UPDATE), иначе возможна гонка
    на MAX(event_id).
    """
    next_id = await _get_next_event_id(session, operation_id)
    event = Event(
        operation_id=operation_id,
        event_id=next_id,
        type=event_type,
        from_status=from_status,
        to_status=to_status,
        message=message,
        occurred_at=datetime.now(timezone.utc),
    )
    session.add(event)
    return event

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

# ── POST /operations/{id}/submit ───────────────────────────────────────────

@router.post("/{operation_id}/submit")
async def submit_operation(
    operation_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """
    Надёжно запланировать отправку операции провайдеру.

    Логика:
    1. Блокируем строку операции (SELECT ... FOR UPDATE).
    2. Если статус != CREATED — возвращаем 200 с текущим состоянием.
    3. Если статус == CREATED:
       a. Вставляем запись в submit_intents.
       b. Меняем статус на PROCESSING.
       c. Записываем событие PROCESSING.
       d. Коммитим транзакцию.
       e. ПОСЛЕ коммита вызываем провайдера.
       f. При успехе сохраняем provider_payment_id.
    """
    # 1. Блокируем строку операции для синхронизации конкурентных запросов.
    #    Блокировка держится ТОЛЬКО на время транзакции (до коммита),
    #    а не на время внешнего HTTP-вызова.
    stmt = (
        select(Operation)
        .where(Operation.operation_id == operation_id)
        .with_for_update()
    )
    result = await session.execute(stmt)
    operation = result.scalar_one_or_none()

    if operation is None:
        raise HTTPException(
            status_code=404,
            detail=f"Операция с operationId='{operation_id}' не найдена",
        )

    # 2. Если статус уже не CREATED — возвращаем текущее состояние (200 OK).
    if operation.status != OperationStatus.CREATED:
        return SubmitResponse.model_validate(operation)

    # 3. Операция в CREATED — атомарно переводим в PROCESSING.
    now = datetime.now(timezone.utc)

    # a. Вставляем submit_intent
    submit_intent = SubmitIntent(
        operation_id=operation_id,
        created_at=now,
    )
    session.add(submit_intent)

    # b. Меняем статус
    from_status = operation.status  # CREATED
    operation.status = OperationStatus.PROCESSING
    operation.updated_at = now

    # c. Записываем событие
    await _add_event(
        session=session,
        operation_id=operation_id,
        event_type="PROCESSING",
        from_status=from_status,
        to_status=OperationStatus.PROCESSING,
        message="Submit intent saved",
    )

    # d. Коммит транзакции.
    #    После этого блокировка снимается, и другие запросы
    #    увидят статус PROCESSING и вернут 200.
    await session.commit()

    # e. ПОСЛЕ коммита: вызываем провайдера.
    #    Получаем ProviderClient из app-состояния.
    provider: ProviderClient = request.app.state.provider_client
    background = request.app.state.background_processor

    # Регистрируем попытку
    background.record_attempt(operation_id)

    result = await provider.create_payment(
        operation_id=operation_id,
        amount=operation.amount,
        currency=operation.currency,
        attempt=1,
    )

    # f. При успехе сохраняем provider_payment_id в отдельной транзакции.
    if result.success and result.provider_payment_id is not None:
        await _save_provider_id_after_submit(
            operation_id, result.provider_payment_id
        )

    return SubmitResponse.model_validate(operation)

async def _save_provider_id_after_submit(
    operation_id: str, provider_payment_id: str
) -> None:
    """
    Сохранить provider_payment_id после успешного вызова провайдера.

    Использует отдельную сессию — исходная транзакция уже закоммичена.
    Проверяет, что операция всё ещё в PROCESSING: квитанция могла прийти
    раньше и перевести операцию в финальный статус. В таком случае
    provider_payment_id НЕ перезаписывается (квитанция уже установила его).
    """
    from app.database import async_session as _session_factory

    async with _session_factory() as new_session:
        stmt = (
            sa_update(Operation)
            .where(
                Operation.operation_id == operation_id,
                Operation.status == OperationStatus.PROCESSING,
            )
            .values(provider_payment_id=provider_payment_id)
        )
        result = await new_session.execute(stmt)
        await new_session.commit()

        if result.rowcount > 0:
            print(
                f"[submit] {operation_id}: "
                f"provider_payment_id={provider_payment_id} сохранён"
            )
        else:
            print(
                f"[submit] {operation_id}: provider_payment_id НЕ сохранён "
                f"(операция уже в финальном статусе — квитанция пришла раньше)"
            )
