# app/background.py
"""
Фоновый обработчик незавершённых операций.

Обрабатывает PROCESSING-операции:
  - Без provider_payment_id: вызывает провайдера.
  - С provider_payment_id: ждёт квитанцию (проверяет, не зависла ли).

При старте приложения загружает все PROCESSING-операции из БД
(восстановление после перезапуска).
"""

from __future__ import annotations

import asyncio
import random
import time

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_processing_operations
from app.models import Operation, OperationStatus
from app.provider_client import ProviderClient
from app.logging_config import get_logger

logger = get_logger(__name__)

# Максимальное количество попыток вызова провайдера для одной операции.
# После исчерпания попыток операция остаётся в PROCESSING навсегда —
# callback-квитанция может прийти в любой момент.
MAX_ATTEMPTS = 20

class BackgroundProcessor:
    """
    Фоновый обработчик PROCESSING-операций.

    Жизненный цикл:
      1. start() — запускает цикл и сразу загружает операции из БД.
      2. _loop() — бесконечно опрашивает БД и обрабатывает операции.
      3. stop() — корректно завершает цикл с ожиданием активных задач.
    """

    def __init__(self, provider_client: ProviderClient) -> None:
        self._provider = provider_client
        self._running = False
        self._task: asyncio.Task | None = None

        # Словарь попыток: operation_id → номер следующей попытки
        self._attempts: dict[str, int] = {}

        # Словарь времени следующей разрешённой попытки: operation_id → timestamp
        # Используется для реализации backoff между попытками
        self._next_attempt_at: dict[str, float] = {}

        # Множество operation_id, для которых provider_payment_id уже сохранён.
        # Эти операции не нужно слать провайдеру — только ждать квитанцию.
        self._has_provider_id: set[str] = set()

    # ── Управление жизненным циклом ─────────────────────────────────────

    async def start(self) -> None:
        """Запустить фоновый цикл."""
        if self._running:
            return
        self._running = True

        # Восстановление после перезапуска: загружаем все PROCESSING-операции
        await self._recover_operations()

        self._task = asyncio.create_task(self._loop())
        logger.info("Фоновый обработчик запущен")

    async def stop(self) -> None:
        """
        Корректно остановить фоновый цикл.

        Дожидается завершения текущей итерации, не обрывает
        активные HTTP-вызовы на полпути.
        """
        logger.info("Остановка фонового обработчика...")
        self._running = False
        if self._task is not None:
            # Даём задаче до 30 секунд на завершение текущей итерации
            try:
                await asyncio.wait_for(self._task, timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "Фоновый обработчик не завершился за 30с, отмена задачи"
                )
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None
        logger.info("Фоновый обработчик остановлен")

    async def _recover_operations(self) -> None:
        """
        Восстановить PROCESSING-операции после перезапуска.

        Загружает все операции в статусе PROCESSING из БД
        и добавляет их в отслеживание.
        """
        operations = await get_processing_operations()

        if not operations:
            logger.info("Нет PROCESSING-операций для восстановления")
            return

        logger.info(
            "Восстановление %d PROCESSING-операций после перезапуска",
            len(operations),
        )

        for op in operations:
            op_id = op.operation_id
            # Если provider_payment_id уже сохранён, не нужно вызывать провайдера
            if op.provider_payment_id is not None:
                self._has_provider_id.add(op_id)
                logger.info(
                    "Восстановлена операция (есть providerPaymentId), ждём квитанцию",
                    extra={"operation_id": op_id},
                )
            else:
                # Начинаем с попытки 1 и разрешаем вызов сразу
                self._attempts[op_id] = 1
                self._next_attempt_at[op_id] = 0.0
                logger.info(
                    "Восстановлена операция (нет providerPaymentId), будет вызван провайдер",
                    extra={"operation_id": op_id},
                )

    # ── Регистрация попыток ─────────────────────────────────────────────

    def record_attempt(self, operation_id: str) -> None:
        """
        Запомнить, что для операции была совершена синхронная попытка.

        Вызывается из роутера submit после первого вызова провайдера.
        """
        if operation_id not in self._attempts:
            self._attempts[operation_id] = 2  # Следующая попытка — вторая

    # ── Главный цикл ────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """
        Главный цикл: опрос БД и обработка PROCESSING-операций.

        Интервал между итерациями: 5 секунд.
        При остановке (_running=False) цикл завершается не позже чем через 1с.
        """
        while self._running:
            try:
                await self._process_pending()
            except Exception as exc:
                logger.exception("Ошибка в цикле фоновой обработки")

            # Ждём между итерациями с проверкой флага каждую секунду
            for _ in range(5):
                if not self._running:
                    break
                await asyncio.sleep(1)

    # ── Обработка pending-операций ──────────────────────────────────────

    async def _process_pending(self) -> None:
        """
        Найти PROCESSING-операции без provider_payment_id и обработать их.
        """
        async with async_session() as session:
            stmt = (
                select(Operation)
                .where(
                    Operation.status == OperationStatus.PROCESSING,
                    Operation.provider_payment_id.is_(None),
                )
            )
            result = await session.execute(stmt)
            operations = result.scalars().all()

        if not operations:
            return

        now = time.monotonic()
        eligible = [
            op for op in operations
            if now >= self._next_attempt_at.get(op.operation_id, 0.0)
        ]

        if not eligible:
            return

        logger.info(
            "PROCESSING-операций всего: %d, готовы к повтору: %d",
            len(operations),
            len(eligible),
        )

        for operation in eligible:
            await self._process_one(operation)

    async def _process_one(self, operation: Operation) -> None:
        """
        Обработать одну PROCESSING-операцию: вызвать провайдера.
        """
        op_id = operation.operation_id

        # Пропускаем, если provider_payment_id уже сохранён
        if op_id in self._has_provider_id:
            return

        attempt = self._attempts.get(op_id, 1)

        # Проверяем лимит попыток
        if attempt > MAX_ATTEMPTS:
            logger.warning(
                "Превышен лимит попыток (%d), операция остаётся в PROCESSING",
                MAX_ATTEMPTS,
                extra={"operation_id": op_id},
            )
            return

        logger.info(
            "Вызов провайдера",
            extra={"operation_id": op_id, "attempt": attempt},
        )

        result = await self._provider.create_payment(
            operation_id=op_id,
            amount=operation.amount,
            currency=operation.currency,
            attempt=attempt,
        )

        if result.success and result.provider_payment_id is not None:
            saved = await self._save_provider_payment_id(
                op_id, result.provider_payment_id
            )
            if saved:
                self._has_provider_id.add(op_id)
                self._attempts.pop(op_id, None)
                self._next_attempt_at.pop(op_id, None)
                logger.info(
                    "provider_payment_id успешно сохранён",
                    extra={
                        "operation_id": op_id,
                        "provider_payment_id": result.provider_payment_id,
                    },
                )

        elif result.retryable:
            # Ошибка, можно повторить — планируем следующую попытку
            self._attempts[op_id] = attempt + 1
            delay = self._backoff_delay(attempt)
            self._next_attempt_at[op_id] = time.monotonic() + delay
            logger.info(
                "Ошибка провайдера, запланирован повтор",
                extra={
                    "operation_id": op_id,
                    "attempt": attempt,
                    "next_delay_sec": round(delay, 1),
                },
            )
        else:
            # Не-retryable ошибка — логируем, но не повторяем
            logger.error(
                "Не-retryable ошибка: %s",
                result.detail,
                extra={"operation_id": op_id, "attempt": attempt},
            )

    # ── Сохранение provider_payment_id ──────────────────────────────────

    async def _save_provider_payment_id(
        self, operation_id: str, provider_payment_id: str
    ) -> bool:
        """
        Сохранить provider_payment_id условным UPDATE.

        Возвращает True, если обновление затронуло строку.
        Возвращает False, если операция уже не в PROCESSING.
        """
        async with async_session() as session:
            stmt = (
                update(Operation)
                .where(
                    Operation.operation_id == operation_id,
                    Operation.status == OperationStatus.PROCESSING,
                )
                .values(provider_payment_id=provider_payment_id)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    # ── Backoff ─────────────────────────────────────────────────────────

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        """
        Экспоненциальный backoff с jitter.
        ...
        максимум: 60с
        """
        base = min(1.5 ** attempt, 60.0)
        jitter = 0.75 + random.random() * 0.5
        return base * jitter
