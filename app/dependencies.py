# app/dependencies.py
"""
Зависимости FastAPI (Depends).

Вынесены в отдельный модуль, чтобы роутеры не импортировали database.py напрямую.
"""

from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from .database import async_session


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Генератор сессий БД.

    Каждый HTTP-запрос получает свою сессию.
    Сессия автоматически закрывается при завершении запроса.
    При ошибке транзакция откатывается.
    """
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
