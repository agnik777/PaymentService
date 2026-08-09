# tests/conftest.py
"""
Фикстуры pytest для тестов candidate-service.

Предоставляют:
  - Тестовый HTTP-клиент (синхронный TestClient) с замоканной БД.
  - Переопределение зависимости get_session → мок-сессия.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from app.app import app
from app.dependencies import get_session

@pytest.fixture(autouse=True)
def override_db_session():
    """
    Переопределить get_session на мок-сессию для ВСЕХ тестов.

    autouse=True означает, что эта фикстура автоматически
    применяется к каждому тесту без явного указания.

    Возвращает словарь с ключами 'session' и 'execute_result',
    чтобы тесты могли настраивать поведение моков.
    """
    # Создаём результат execute — его будут настраивать тесты
    execute_result = MagicMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=None)
    execute_result.scalars = MagicMock(
        return_value=MagicMock(all=MagicMock(return_value=[]))
    )
    execute_result.scalar_one = MagicMock(return_value=0)
    execute_result.rowcount = 0

    # Создаём сессию
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)

    # execute() будет возвращать настроенный execute_result
    async def _execute(*args, **kwargs):
        return execute_result

    session.execute = _execute
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()

    # Переопределяем зависимость
    app.dependency_overrides[get_session] = lambda: session

    result = {
        "session": session,
        "execute_result": execute_result,
    }

    yield result

    # Очищаем переопределения после тестов
    app.dependency_overrides.clear()

@pytest.fixture
def client(override_db_session):
    """
    Синхронный TestClient с замоканной БД.

    Все запросы к БД идут через мок-сессию из фикстуры override_db_session.
    """
    return TestClient(app)

@pytest.fixture
def mock_session(override_db_session):
    """
    Вернуть мок-сессию для тестов, которым нужно настраивать поведение.

    Использование:
        def test_something(mock_session):
            mock_session.get.return_value = fake_operation
            ...
    """
    return override_db_session["session"]

@pytest.fixture
def execute_result(override_db_session):
    """
    Вернуть мок-объект результата execute().

    Использование:
        def test_something(execute_result):
            execute_result.scalar_one_or_none.return_value = fake_op
            ...
    """
    return override_db_session["execute_result"]
