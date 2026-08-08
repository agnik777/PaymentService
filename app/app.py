# app/app.py
"""
candidate-service — платёжный сервис-посредник.

Точка входа FastAPI-приложения.
Собирает роутеры и управляет жизненным циклом.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import config
from .database import create_tables, close_db
from app.routers.health import router as health_router
from app.routers.operations import router as operations_router

# ── Lifespan (startup / shutdown) ──────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Контекст жизненного цикла приложения.

    При старте:
      - Создаёт таблицы в БД (если их нет).

    При завершении:
      - Закрывает пул соединений с БД.
    """
    print("[lifespan] candidate-service запускается...")
    print(f"[lifespan] PROVIDER_URL = {config.PROVIDER_URL}")

    # Создаём таблицы (CREATE TABLE IF NOT EXISTS)
    print("[lifespan] Создание таблиц в БД...")
    await create_tables()
    print("[lifespan] Таблицы созданы (или уже существовали).")

    yield  # Приложение работает

    print("[lifespan] candidate-service завершает работу...")
    await close_db()
    print("[lifespan] Пул соединений с БД закрыт.")


app = FastAPI(
    title="candidate-service",
    description="Payment Service",
    version="0.0.1",
    lifespan=lifespan,
)


# Подключение роутеров
app.include_router(health_router)
app.include_router(operations_router)
