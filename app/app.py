# app/app.py
"""
candidate-service — платёжный сервис-посредник.

Точка входа FastAPI-приложения.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import config
from .database import check_db_connection, create_tables, close_db

# ── Lifespan (startup / shutdown) ──────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Контекст жизненного цикла приложения.

    При старте:
      - Создаёт таблицы в БД (если их нет).
      - Выводит информационные сообщения.

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


@app.get("/health")
async def health():
    """
    Проверка готовности сервиса.

    Проверяет подключение к БД.
    Возвращает 200 OK, если всё в порядке.
    Возвращает 503 Service Unavailable, если БД недоступна.
    """
    db_ok = await check_db_connection()

    if db_ok:
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "database": "connected"},
        )

    return JSONResponse(
        status_code=503,
        content={"status": "error", "database": "disconnected"},
    )
