# tests/test_receipts.py
"""
Юнит-тесты для эндпоинта /receipts.

Тестируют:
  - Валидацию ReceiptRequest (Pydantic).
  - Разбор граничных случаев тела запроса.
"""

import pytest
from datetime import datetime, timezone

from app.schemas import ReceiptRequest


class TestReceiptRequestValidation:
    """Тесты Pydantic-валидации для ReceiptRequest."""

    def test_valid_completed(self):
        """Валидная квитанция с COMPLETED."""
        req = ReceiptRequest.model_validate({
            "providerPaymentId": "aa5b7856-e9f2-4fd5-955b-38b1f28d9c57",
            "operationId": "op-1",
            "result": "COMPLETED",
            "message": "Платёж завершён",
            "occurredAt": "2026-08-06T12:00:00Z",
        })
        assert req.result == "COMPLETED"
        assert req.provider_payment_id == "aa5b7856-e9f2-4fd5-955b-38b1f28d9c57"

    def test_valid_rejected(self):
        """Валидная квитанция с REJECTED."""
        req = ReceiptRequest.model_validate({
            "providerPaymentId": "bb6c8967-f0a3-5ge6-a66c-49c2g39e0d68",
            "operationId": "op-2",
            "result": "REJECTED",
            "message": "Отказ провайдера",
            "occurredAt": "2026-08-06T13:00:00Z",
        })
        assert req.result == "REJECTED"

    def test_result_case_insensitive(self):
        """rejected → REJECTED."""
        req = ReceiptRequest.model_validate({
            "providerPaymentId": "aa5b7856-e9f2-4fd5-955b-38b1f28d9c57",
            "operationId": "op-3",
            "result": "rejected",
            "message": "",
            "occurredAt": "2026-08-06T12:00:00Z",
        })
        assert req.result == "REJECTED"

    def test_invalid_result(self):
        """Недопустимый result."""
        with pytest.raises(Exception):
            ReceiptRequest.model_validate({
                "providerPaymentId": "aa5b7856-e9f2-4fd5-955b-38b1f28d9c57",
                "operationId": "op-4",
                "result": "PENDING",
                "message": "",
                "occurredAt": "2026-08-06T12:00:00Z",
            })

    def test_missing_provider_payment_id(self):
        """providerPaymentId обязателен."""
        with pytest.raises(Exception):
            ReceiptRequest.model_validate({
                "operationId": "op-5",
                "result": "COMPLETED",
                "message": "",
                "occurredAt": "2026-08-06T12:00:00Z",
            })

    def test_missing_operation_id(self):
        """operationId обязателен."""
        with pytest.raises(Exception):
            ReceiptRequest.model_validate({
                "providerPaymentId": "aa5b7856-e9f2-4fd5-955b-38b1f28d9c57",
                "result": "COMPLETED",
                "message": "",
                "occurredAt": "2026-08-06T12:00:00Z",
            })

    def test_optional_message(self):
        """message может быть опущен."""
        req = ReceiptRequest.model_validate({
            "providerPaymentId": "aa5b7856-e9f2-4fd5-955b-38b1f28d9c57",
            "operationId": "op-6",
            "result": "COMPLETED",
            "occurredAt": "2026-08-06T12:00:00Z",
        })
        assert req.message is None

    def test_datetime_parsing(self):
        """occurredAt парсится в datetime."""
        req = ReceiptRequest.model_validate({
            "providerPaymentId": "aa5b7856-e9f2-4fd5-955b-38b1f28d9c57",
            "operationId": "op-7",
            "result": "COMPLETED",
            "occurredAt": "2026-08-06T12:00:00+03:00",
        })
        assert isinstance(req.occurred_at, datetime)
