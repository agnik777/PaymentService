# app/database.py

"""
Подключение к PostgreSQL: асинхронный движок, сессии, создание таблиц.
Этот модуль — единственная точка входа для работы с БД.
Все эндпоинты импортируют get_session отсюда.
"""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import text

from .config import config

# Асинхронный движок SQLAlchemy
# pool_size=10 — максимальное количество одновременных соединений
# echo=True включает логирование SQL-запросов (для отладки)
engine = create_async_engine(
    config.DATABASE_URL,
    pool_size=10,
    echo=False,  # Поставьте True для отладки SQL
)

# Фабрика сессий
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Не сбрасывать загруженные объекты после commit
)

async def check_db_connection() -> bool:
    """
    Проверка подключения к БД: выполняет SELECT 1.
    Возвращает True, если БД доступна, иначе False.
    Используется в /health для проверки готовности.
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False

async def create_tables() -> None:
    """
    Создаёт все таблицы, описанные в моделях, если они ещё не существуют.
    Вызывается при старте приложения (в lifespan).
    Использует MetaData.create_all — это не полноценная миграция,
    но для тестового задания допустимо.
    """
    from app.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def close_db() -> None:
    """
    Закрывает пул соединений с БД.
    Вызывается при завершении приложения (в lifespan).
    """
    await engine.dispose()
