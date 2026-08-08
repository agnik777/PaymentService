# app/background.py
"""
Фоновый обработчик незавершённых операций.

После того как операция переведена в PROCESSING, она попадает в очередь
фонового обработчика. Обработчик:
  - Находит все PROCESSING-операции без provider_payment_id.
  - Вызывает провайдера с Idempotency-Key.
  - При успехе сохраняет provider_payment_id.
  - При ошибке планирует повтор с экспоненциальным backoff и jitter.

Операции, у которых provider_payment_id УЖЕ сохранён, не обрабатываются
фоновым обработчиком — они ждут только callback-квитанции.
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

# Максимальное количество попыток вызова провайдера для одной операции.
# После исчерпания попыток операция остаётся в PROCESSING навсегда —
# это осознанное решение: операция не должна «сдаваться», потому что
# callback-квитанция может прийти в любой момент.
MAX_ATTEMPTS = 20

class BackgroundProcessor:
    """
    Фоновый обработчик PROCESSING-операций.

    Запускается при старте приложения, работает в бесконечном цикле
    с интервалом опроса. При остановке приложения корректно завершается.

    Отслеживает попытки в оперативной памяти. После перезапуска счётчики
    сбрасываются — это допустимо, потому что Idempotency-Key защищает
    от дублирования платежей.
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

    # ── Управление жизненным циклом ─────────────────────────────────────

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

    # ── Регистрация попыток ─────────────────────────────────────────────

    def record_attempt(self, operation_id: str) -> None:
        """
        Запомнить, что для операции была совершена попытка отправки.

        Вызывается из роутера при первом submit (попытка 1 уже сделана
        синхронно в эндпоинте, фоновый обработчик начнёт с попытки 2).
        """
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
                print(f"[background] Ошибка в цикле обработки: {exc}")

            # Ждём между итерациями с проверкой флага каждую секунду
            for _ in range(5):
                if not self._running:
                    break
                await asyncio.sleep(1)

    # ── Обработка pending-операций ──────────────────────────────────────

    async def _process_pending(self) -> None:
        """
        Найти все PROCESSING-операции без provider_payment_id
        и попытаться вызвать для них провайдера.

        Операции, у которых не подошло время следующей попытки
        (backoff), пропускаются.
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

        print(
            f"[background] Найдено PROCESSING-операций: {len(operations)}, "
            f"из них готовы к повтору: {len(eligible)}"
        )

        for operation in eligible:
            await self._process_one(operation)

    async def _process_one(self, operation: Operation) -> None:
        """
        Обработать одну PROCESSING-операцию: вызвать провайдера.
        """
        op_id = operation.operation_id
        attempt = self._attempts.get(op_id, 1)

        # Проверяем лимит попыток
        if attempt > MAX_ATTEMPTS:
            print(
                f"[background] {op_id}: превышен лимит попыток ({MAX_ATTEMPTS}), "
                f"больше не повторяем. Операция остаётся в PROCESSING."
            )
            return

        print(f"[background] Обработка {op_id}, попытка {attempt}/{MAX_ATTEMPTS}")

        result = await self._provider.create_payment(
            operation_id=op_id,
            amount=operation.amount,
            currency=operation.currency,
            attempt=attempt,
        )

        if result.success and result.provider_payment_id is not None:
            # Успех — сохраняем provider_payment_id
            await self._save_provider_payment_id(op_id, result.provider_payment_id)
            # Очищаем счётчики для этой операции
            self._attempts.pop(op_id, None)
            self._next_attempt_at.pop(op_id, None)

        elif result.retryable:
            # Ошибка, можно повторить — планируем следующую попытку
            self._attempts[op_id] = attempt + 1
            delay = self._backoff_delay(attempt)
            self._next_attempt_at[op_id] = time.monotonic() + delay
            print(
                f"[background] {op_id}: ошибка (попытка {attempt}), "
                f"повтор через {delay:.1f}с"
            )
        else:
            # Не-retryable ошибка — логируем, но не повторяем
            print(
                f"[background] {op_id}: не-retryable ошибка на попытке {attempt}: "
                f"{result.detail}"
            )

    # ── Сохранение provider_payment_id ──────────────────────────────────

    async def _save_provider_payment_id(
        self, operation_id: str, provider_payment_id: str
    ) -> None:
        """
        Сохранить provider_payment_id для операции.

        Использует условный UPDATE: меняет только если статус PROCESSING
        (квитанция могла уже прийти и перевести операцию в финальный статус).
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

            if result.rowcount > 0:
                print(
                    f"[background] {operation_id}: "
                    f"provider_payment_id={provider_payment_id} сохранён"
                )
            else:
                print(
                    f"[background] {operation_id}: provider_payment_id НЕ сохранён "
                    f"(операция уже не в PROCESSING — квитанция пришла раньше)"
                )

    # ── Backoff ─────────────────────────────────────────────────────────

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        """
        Экспоненциальный backoff с jitter.

        Формула: min(1.5^attempt, 60) × random(0.75, 1.25)

        attempt 1 (сразу после синхронного вызова): ~1.1–1.9с
        attempt 2: ~1.7–2.8с
        attempt 3: ~2.5–4.2с
        attempt 4: ~3.8–6.3с
        attempt 5: ~5.7–9.5с
        ...
        максимум: 60с
        """
        base = min(1.5 ** attempt, 60.0)
        jitter = 0.75 + random.random() * 0.5  # 0.75 .. 1.25
        return base * jitter
