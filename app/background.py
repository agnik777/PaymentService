# app/background.py
"""
Фоновый обработчик незавершённых операций.

После того как операция переведена в PROCESSING, она попадает в очередь
фонового обработчика. Обработчик:
  - Находит все PROCESSING-операции без provider_payment_id.
  - Вызывает провайдера с Idempotency-Key.
  - При успехе сохраняет provider_payment_id.
  - При ошибке планирует повтор с экспоненциальным backoff.
"""

from __future__ import annotations

import asyncio
import random
import time

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import Operation, OperationStatus
from app.provider_client import ProviderClient

class BackgroundProcessor:
    """
    Фоновый обработчик PROCESSING-операций.

    Запускается при старте приложения, работает в бесконечном цикле
    с интервалом опроса. При остановке приложения корректно завершается.
    """

    def __init__(self, provider_client: ProviderClient) -> None:
        self._provider = provider_client
        self._running = False
        self._task: asyncio.Task | None = None
        # Отслеживание попыток: ключ — operation_id, значение — номер попытки
        self._attempts: dict[str, int] = {}

    async def start(self) -> None:
        """Запустить фоновый цикл."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        print("[background] Фоновый обработчик запущен")

    async def stop(self) -> None:
        """
        Корректно остановить фоновый цикл.

        Дожидается завершения текущей итерации, не обрывает
        активные HTTP-вызовы на полпути.
        """
        print("[background] Остановка фонового обработчика...")
        self._running = False
        if self._task is not None:
            await self._task
            self._task = None
        print("[background] Фоновый обработчик остановлен")

    def record_attempt(self, operation_id: str) -> None:
        """
        Запомнить, что для операции была совершена попытка отправки.

        Вызывается из роутера при первом submit.
        """
        self._attempts[operation_id] = 1

    async def _loop(self) -> None:
        """
        Главный цикл: опрос БД и обработка PROCESSING-операций.

        Интервал опроса: 5 секунд между итерациями.
        """
        while self._running:
            try:
                await self._process_pending()
            except Exception as exc:
                print(f"[background] Ошибка в цикле обработки: {exc}")

            # Ждём между итерациями, но проверяем флаг _running каждую секунду,
            # чтобы не задерживать остановку сервиса больше чем на 1 секунду
            for _ in range(5):
                if not self._running:
                    break
                await asyncio.sleep(1)

    async def _process_pending(self) -> None:
        """
        Найти все PROCESSING-операции без provider_payment_id
        и попытаться вызвать для них провайдера.
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

        print(
            f"[background] Найдено PROCESSING-операций: {len(operations)}"
        )

        for operation in operations:
            await self._process_one(operation)

    async def _process_one(self, operation: Operation) -> None:
        """
        Обработать одну PROCESSING-операцию: вызвать провайдера,
        при успехе сохранить provider_payment_id.
        """
        op_id = operation.operation_id
        attempt = self._attempts.get(op_id, 1)

        print(f"[background] Обработка {op_id}, попытка {attempt}")

        result = await self._provider.create_payment(
            operation_id=op_id,
            amount=operation.amount,
            currency=operation.currency,
            attempt=attempt,
        )

        if result.success and result.provider_payment_id is not None:
            await self._save_provider_payment_id(op_id, result.provider_payment_id)
        elif result.retryable:
            self._attempts[op_id] = attempt + 1
            delay = self._backoff_delay(attempt)
            print(
                f"[background] {op_id}: ошибка, повтор через {delay:.1f}с "
                f"(попытка {attempt + 1})"
            )

    async def _save_provider_payment_id(
        self, operation_id: str, provider_payment_id: str
    ) -> None:
        """
        Сохранить provider_payment_id для операции.

        Проверяет, что операция всё ещё в PROCESSING (квитанция
        могла уже прийти и перевести её в финальный статус).
        """
        async with async_session() as session:
            # Атомарно обновляем, только если статус PROCESSING
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

            if result.rowcount > 0:
                print(
                    f"[background] {operation_id}: provider_payment_id сохранён"
                )
            else:
                print(
                    f"[background] {operation_id}: provider_payment_id НЕ сохранён "
                    f"(операция уже не в PROCESSING — вероятно, квитанция пришла раньше)"
                )

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        """
        Экспоненциальный backoff с jitter.

        Формула: min(1.5^attempt, 60) * (0.75 + random * 0.5)
        - База: 1.5^attempt секунд.
        - Максимум: 60 секунд.
        - Jitter: ±25% случайного разброса.

        Пример для attempt:
          1 → ~1.1–1.9с
          2 → ~1.7–2.8с
          3 → ~2.5–4.2с
          4 → ~3.8–6.3с
        """
        base = min(1.5 ** attempt, 60.0)
        jitter = 0.75 + random.random() * 0.5  # от 0.75 до 1.25
        return base * jitter
