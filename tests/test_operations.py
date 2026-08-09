# tests/test_operations.py
"""
Юнит-тесты для эндпоинтов /operations.

Тестируют:
  - Валидацию входных данных (Pydantic).
  - HTTP-коды ответов на уровне контракта.
  - Базовые сценарии создания и получения.

Не тестируют: конкурентность (требует реальной БД),
фоновый обработчик (интеграционный тест).
"""

import pytest
from datetime import datetime, timezone

from app.schemas import CreateOperationRequest
from app.models import Operation, OperationStatus

class TestCreateOperationValidation:
    """Тесты Pydantic-валидации для CreateOperationRequest."""

    def test_valid_request_minimal(self):
        req = CreateOperationRequest.model_validate({
            "operationId": "test-1",
            "amount": "100",
            "currency": "RUB",
        })
        assert req.operation_id == "test-1"
        assert req.amount == "100"
        assert req.currency == "RUB"
        assert req.description is None

    def test_valid_request_full(self):
        req = CreateOperationRequest.model_validate({
            "operationId": "test-2",
            "amount": "1500.50",
            "currency": "RUB",
            "description": "Тестовый платёж",
        })
        assert req.description == "Тестовый платёж"

    def test_valid_amount_integer(self):
        req = CreateOperationRequest.model_validate({
            "operationId": "t",
            "amount": "42",
            "currency": "RUB",
        })
        assert req.amount == "42"

    def test_valid_amount_one_decimal(self):
        req = CreateOperationRequest.model_validate({
            "operationId": "t",
            "amount": "100.5",
            "currency": "RUB",
        })
        assert req.amount == "100.5"

    def test_valid_amount_two_decimals(self):
        req = CreateOperationRequest.model_validate({
            "operationId": "t",
            "amount": "100.50",
            "currency": "RUB",
        })
        assert req.amount == "100.50"

    def test_invalid_amount_negative(self):
        with pytest.raises(Exception):
            CreateOperationRequest.model_validate({
                "operationId": "t",
                "amount": "-1.00",
                "currency": "RUB",
            })

    def test_invalid_amount_three_decimals(self):
        with pytest.raises(Exception):
            CreateOperationRequest.model_validate({
                "operationId": "t",
                "amount": "1.001",
                "currency": "RUB",
            })

    def test_invalid_amount_zero(self):
        with pytest.raises(Exception):
            CreateOperationRequest.model_validate({
                "operationId": "t",
                "amount": "0",
                "currency": "RUB",
            })

    def test_invalid_amount_empty(self):
        with pytest.raises(Exception):
            CreateOperationRequest.model_validate({
                "operationId": "t",
                "amount": "",
                "currency": "RUB",
            })

    def test_invalid_currency_usd(self):
        with pytest.raises(Exception):
            CreateOperationRequest.model_validate({
                "operationId": "t",
                "amount": "100",
                "currency": "USD",
            })

    def test_invalid_currency_eur(self):
        with pytest.raises(Exception):
            CreateOperationRequest.model_validate({
                "operationId": "t",
                "amount": "100",
                "currency": "EUR",
            })

    def test_currency_case_insensitive(self):
        req = CreateOperationRequest.model_validate({
            "operationId": "t",
            "amount": "100",
            "currency": "rub",
        })
        assert req.currency == "RUB"

    def test_missing_operation_id(self):
        with pytest.raises(Exception):
            CreateOperationRequest.model_validate({
                "amount": "100",
                "currency": "RUB",
            })

    def test_missing_amount(self):
        with pytest.raises(Exception):
            CreateOperationRequest.model_validate({
                "operationId": "t",
                "currency": "RUB",
            })

    def test_operation_id_too_long(self):
        with pytest.raises(Exception):
            CreateOperationRequest.model_validate({
                "operationId": "a" * 129,
                "amount": "100",
                "currency": "RUB",
            })

    def test_description_too_long(self):
        with pytest.raises(Exception):
            CreateOperationRequest.model_validate({
                "operationId": "t",
                "amount": "100",
                "currency": "RUB",
                "description": "x" * 513,
            })

class TestCreateOperationHTTP:
    """Тесты HTTP-уровня для POST /operations (с замоканной БД)."""

    def test_create_returns_201(self, client, mock_session, execute_result):
        """Создание возвращает 201."""
        mock_session.get.return_value = None

        response = client.post("/operations", json={
            "operationId": "test-http-1",
            "amount": "100.00",
            "currency": "RUB",
        })

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "CREATED"
        assert data["operationId"] == "test-http-1"
        assert data["providerPaymentId"] is None

    def test_create_duplicate_returns_409(self, client, mock_session):
        """Дубликат возвращает 409."""
        existing = Operation(
            operation_id="test-dup",
            amount="50.00",
            currency="RUB",
            status=OperationStatus.CREATED,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        mock_session.get.return_value = existing

        response = client.post("/operations", json={
            "operationId": "test-dup",
            "amount": "999.99",
            "currency": "RUB",
        })

        assert response.status_code == 409
        assert "уже существует" in response.json()["detail"]

    def test_validation_error_returns_422(self, client):
        """Некорректный amount → 422 (без участия БД)."""
        response = client.post("/operations", json={
            "operationId": "test",
            "amount": "-1.00",
            "currency": "RUB",
        })
        assert response.status_code == 422
        

class TestGetOperationHTTP:
    """Тесты GET /operations/{id} (с замоканной БД)."""

    def test_nonexistent_returns_404(self, client):
        """
        Несуществующая операция → 404.

        execute_result.scalar_one_or_none по умолчанию возвращает None
        (настроено в conftest.py), поэтому _get_operation_or_404
        выбрасывает 404.
        """
        response = client.get("/operations/nonexistent")
        assert response.status_code == 404
        assert "не найдена" in response.json()["detail"]


class TestHealthEndpoint:
    """Тесты /health."""

    def test_health_returns_503_when_db_down(self, client, mock_session):
        """Health возвращает 503 при недоступной БД."""
        async def _raise(*args, **kwargs):
            raise Exception("DB down")

        mock_session.execute = _raise

        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["status"] == "error"
