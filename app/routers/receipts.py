# app/routers/receipts.py
"""
Роутер приёма callback-квитанций от провайдера.

POST /receipts — единственный эндпоинт.
Обрабатывает все сценарии:
  - Нормальная квитанция → переход в COMPLETED/REJECTED.
  - Ранняя квитанция (до ответа провайдера) → providerPaymentId
    устанавливается из квитанции.
  - Повторная квитанция с тем же результатом → 204, без нового события.
  - Конфликтующая квитанция с противоположным результатом → 204,
    событие IGNORED_DUPLICATE_RECEIPT, статус не меняется.
  - Несовпадающий providerPaymentId после установления связи → 409.
  - Квитанция для несуществующей операции → 404.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_session
from app.models import Operation, OperationStatus, Event
from app.schemas import ReceiptRequest


router = APIRouter(prefix="/receipts", tags=["receipts"])

@router.post("", status_code=204)
async def receive_receipt(
    body: ReceiptRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Принять callback-квитанцию от провайдера.

    Вся обработка выполняется в одной транзакции.
    Всегда возвращает 204 No Content (кроме случаев 404 и 409).

    Правила обработки (строго по условию):

    1. Если операция не найдена → 404.
    2. Если providerPaymentId уже установлен и не совпадает → 409 Conflict.
    3. Если providerPaymentId ещё не установлен → устанавливаем из квитанции.
    4. Если статус уже финальный (COMPLETED/REJECTED):
       - Тот же результат → 204, без нового события.
       - Противоположный результат → 204, событие IGNORED_DUPLICATE_RECEIPT,
         статус НЕ меняем.
    5. Если статус PROCESSING или CREATED:
       - Переводим в COMPLETED или REJECTED (согласно result).
       - Записываем событие перехода.
    """
    # 1. Загружаем операцию с блокировкой строки.
    #    Блокировка гарантирует, что две квитанции для одной операции
    #    не обработаются параллельно и не создадут дублирующих событий.
    stmt = (
        select(Operation)
        .where(Operation.operation_id == body.operation_id)
        .with_for_update()
    )
    result = await session.execute(stmt)
    operation = result.scalar_one_or_none()

    if operation is None:
        raise HTTPException(
            status_code=404,
            detail=f"Операция с operationId='{body.operation_id}' не найдена",
        )

    # 2. Проверка на несовпадение providerPaymentId.
    if (
        operation.provider_payment_id is not None
        and operation.provider_payment_id != body.provider_payment_id
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Несовпадение providerPaymentId: "
                f"ожидался '{operation.provider_payment_id}', "
                f"получен '{body.provider_payment_id}'"
            ),
        )

    # 3. Если providerPaymentId ещё не установлен — устанавливаем.
    provider_id_set_now = False
    if operation.provider_payment_id is None:
        operation.provider_payment_id = body.provider_payment_id
        provider_id_set_now = True

    now = datetime.now(timezone.utc)

    # 4. Если статус уже финальный.
    if operation.status in OperationStatus.FINAL:
        # 4a. Тот же результат — молча принимаем, без нового события.
        if operation.status == body.result:
            await session.commit()
            return Response(status_code=204)

        # 4b. Противоположный результат — фиксируем как проигнорированный.
        await _add_event_static(
            session=session,
            operation_id=body.operation_id,
            event_type="IGNORED_DUPLICATE_RECEIPT",
            from_status=operation.status,
            to_status=operation.status,  # Статус НЕ меняется
            message=(
                f"Получена квитанция с result='{body.result}', "
                f"но операция уже в статусе '{operation.status}'. "
                f"Квитанция проигнорирована."
            ),
            occurred_at=now,
        )
        await session.commit()
        return Response(status_code=204)

    # 5. Статус PROCESSING или CREATED — переводим в финальный.
    from_status = operation.status
    operation.status = body.result
    operation.updated_at = now

    await _add_event_static(
        session=session,
        operation_id=body.operation_id,
        event_type=body.result,  # "COMPLETED" или "REJECTED"
        from_status=from_status,
        to_status=body.result,
        message=(
            f"Receipt received: {body.message or 'No message'}"
        ),
        occurred_at=now,
    )

    await session.commit()
    return Response(status_code=204)

# ── Вспомогательные функции ────────────────────────────────────────────────

async def _get_next_event_id(
    session: AsyncSession, operation_id: str
) -> int:
    """
    Вычислить следующий event_id для операции.

    Используется внутри транзакции с блокировкой строки операции,
    поэтому конкурентные вставки для одной операции невозможны.
    """
    from sqlalchemy import func

    stmt = select(func.coalesce(func.max(Event.event_id), 0)).where(
        Event.operation_id == operation_id
    )
    result = await session.execute(stmt)
    max_id: int = result.scalar_one()
    return max_id + 1

async def _add_event_static(
    session: AsyncSession,
    operation_id: str,
    event_type: str,
    from_status: str | None,
    to_status: str,
    message: str | None,
    occurred_at: datetime,
) -> Event:
    """
    Создать событие с монотонно возрастающим event_id.

    Статическая (не привязанная к классу) версия для использования
    в receipts-роутере. Не дублирует аналогичную функцию из operations.py
    намеренно — каждый роутер сам управляет своими событиями.
    """
    next_id = await _get_next_event_id(session, operation_id)
    event = Event(
        operation_id=operation_id,
        event_id=next_id,
        type=event_type,
        from_status=from_status,
        to_status=to_status,
        message=message,
        occurred_at=occurred_at,
    )
    session.add(event)
    return event
