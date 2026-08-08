# app/provider_client.py
"""
HTTP-клиент для вызова внешнего провайдера (provider-simulator).

Отвечает за:
  - Отправку POST /payments с заголовками идемпотентности.
  - Обработку успешных и неуспешных ответов.
  - Логирование попыток.
"""

from __future__ import annotations

import httpx

from .config import config

class ProviderClient:
    """
    Асинхронный клиент для взаимодействия с симулятором провайдера.
    Использует httpx.AsyncClient с таймаутом на соединение и чтение.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Ленивое создание клиента ( singleton в рамках экземпляра)."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=5.0,   # Таймаут установки соединения
                    read=10.0,     # Таймаут ожидания ответа
                    write=5.0,     # Таймаут отправки запроса
                    pool=5.0,      # Таймаут ожидания соединения из пула
                ),
            )
        return self._client

    async def close(self) -> None:
        """Закрыть HTTP-клиент и освободить ресурсы."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def create_payment(
        self,
        operation_id: str,
        amount: str,
        currency: str,
        attempt: int = 1,
    ) -> PaymentResult:
        """
        Вызвать POST /payments у провайдера.

        Args:
            operation_id: идентификатор операции (он же Idempotency-Key).
            amount: сумма платежа.
            currency: валюта (RUB).
            attempt: номер попытки (для логирования).

        Returns:
            PaymentResult — структура с результатом вызова.

        Raises:
            Не выбрасывает исключений — все ошибки упакованы в PaymentResult.
            Вызывающий код решает, что делать дальше.
        """
        client = await self._get_client()
        url = f"{config.PROVIDER_URL}/payments"

        payload = {
            "operationId": operation_id,
            "amount": amount,
            "currency": currency,
        }

        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": operation_id,
            "X-Correlation-ID": operation_id,
        }

        print(
            f"[provider] Попытка {attempt}: вызов {url} "
            f"для operationId={operation_id}"
        )

        try:
            response = await client.post(url, json=payload, headers=headers)
            status_code = response.status_code
            body = response.json() if response.text else None
        except httpx.TimeoutException:
            print(
                f"[provider] Попытка {attempt}: таймаут "
                f"для operationId={operation_id}"
            )
            return PaymentResult.network_error(
                operation_id=operation_id,
                detail="Таймаут соединения с провайдером",
            )
        except httpx.NetworkError as exc:
            print(
                f"[provider] Попытка {attempt}: сетевая ошибка "
                f"для operationId={operation_id}: {exc}"
            )
            return PaymentResult.network_error(
                operation_id=operation_id,
                detail=f"Сетевая ошибка: {exc}",
            )

        # Успех: 202 Accepted — платёж принят к обработке
        if status_code == 202 and body is not None:
            provider_payment_id = body.get("providerPaymentId")
            print(
                f"[provider] Попытка {attempt}: успех (202) "
                f"operationId={operation_id} "
                f"providerPaymentId={provider_payment_id}"
            )
            return PaymentResult.accepted(
                operation_id=operation_id,
                provider_payment_id=provider_payment_id,
            )

        # Отказ: 503 Service Unavailable — нужно повторить
        if status_code == 503:
            print(
                f"[provider] Попытка {attempt}: 503 "
                f"для operationId={operation_id}"
            )
            return PaymentResult.unavailable(
                operation_id=operation_id,
                detail="Провайдер временно недоступен (503)",
            )

        # Любой другой неожиданный статус
        print(
            f"[provider] Попытка {attempt}: неожиданный статус {status_code} "
            f"для operationId={operation_id}, тело: {body}"
        )
        return PaymentResult.unexpected_status(
            operation_id=operation_id,
            status_code=status_code,
            body=body,
        )

# ── Структура результата вызова провайдера ─────────────────────────────────

class PaymentResult:
    """
    Результат вызова POST /payments.
    Не исключение — вызывающий код принимает решение на основе флагов.
    """

    def __init__(
        self,
        operation_id: str,
        success: bool,
        retryable: bool,
        provider_payment_id: str | None,
        detail: str,
        status_code: int | None,
        body: dict | None,
    ) -> None:
        self.operation_id = operation_id
        self.success = success
        self.retryable = retryable
        self.provider_payment_id = provider_payment_id
        self.detail = detail
        self.status_code = status_code
        self.body = body

    @classmethod
    def accepted(cls, operation_id: str, provider_payment_id: str | None) -> "PaymentResult":
        """Провайдер принял платёж (202)."""
        return cls(
            operation_id=operation_id,
            success=True,
            retryable=False,
            provider_payment_id=provider_payment_id,
            detail="Платёж принят провайдером",
            status_code=202,
            body=None,
        )

    @classmethod
    def network_error(cls, operation_id: str, detail: str) -> "PaymentResult":
        """Сетевая ошибка или таймаут — можно и нужно повторить."""
        return cls(
            operation_id=operation_id,
            success=False,
            retryable=True,
            provider_payment_id=None,
            detail=detail,
            status_code=None,
            body=None,
        )

    @classmethod
    def unavailable(cls, operation_id: str, detail: str) -> "PaymentResult":
        """503 — временная недоступность, можно повторить."""
        return cls(
            operation_id=operation_id,
            success=False,
            retryable=True,
            provider_payment_id=None,
            detail=detail,
            status_code=503,
            body=None,
        )

    @classmethod
    def unexpected_status(
        cls, operation_id: str, status_code: int, body: dict | None
    ) -> "PaymentResult":
        """Неожиданный статус — не повторяем автоматически."""
        return cls(
            operation_id=operation_id,
            success=False,
            retryable=False,
            provider_payment_id=None,
            detail=f"Неожиданный HTTP-статус: {status_code}",
            status_code=status_code,
            body=body,
        )
