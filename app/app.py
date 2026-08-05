# app/app.py
"""
candidate-service — платёжный сервис-посредник.

Точка входа FastAPI-приложения.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

# ── Lifespan (startup / shutdown) ──────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Контекст жизненного цикла приложения.

    На первом этапе только выводит информационные сообщения.
    На последующих этапах здесь будет инициализация БД
    и запуск фоновых обработчиков.
    """
    print("[lifespan] candidate-service запускается...")
    print(f"[lifespan] PROVIDER_URL = {os.getenv('PROVIDER_URL', 'не задан')}")
    yield  # Приложение работает
    print("[lifespan] candidate-service завершает работу...")


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

    Возвращает 200 OK с JSON-объектом, содержащим статус.
    На последующих этапах добавится проверка подключения к БД.
    """
    return JSONResponse(
        status_code=200,
        content={"status": "ok"},
    )
