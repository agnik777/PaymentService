# tests/conftest.py
"""
Фикстуры pytest для тестов candidate-service.

Предоставляют:
  - Тестовый HTTP-клиент (httpx.AsyncClient против FastAPI TestClient).
  - Тестовую БД (in-memory SQLite поверх реальной PostgreSQL сложно,
    поэтому используем моки на уровне сессий).
  - Переопределение зависимостей (get_session → мок-сессия).

Стратегия тестирования:
  - Юнит-тесты: Pydantic-схемы, валидация, ProviderClient (с моком httpx).
  - Интеграционные: эндпоинты через FastAPI TestClient с переопределённой БД.

На реальной БД тесты НЕ гоняем — они требуют поднятого PostgreSQL.
Для этого есть сквозной сценарий в README.md.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.app import app

@pytest.fixture
def client():
    """
    Синхронный TestClient для простых тестов валидации.

    Не делает реальных запросов к БД — только проверяет
    Pydantic-валидацию и HTTP-коды на уровне FastAPI.
    """
    return TestClient(app)

@pytest_asyncio.fixture
async def async_client():
    """
    Асинхронный HTTP-клиент для тестов эндпоинтов.

    Использует ASGITransport — запросы идут напрямую к FastAPI,
    без поднятия реального сервера и порта.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

@pytest.fixture
def mock_session():
    """
    Мок SQLAlchemy AsyncSession.

    Используется для юнит-тестов роутеров: переопределяем Depends(get_session)
    так, чтобы вместо реальной БД использовался этот мок.
    """
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    return session
