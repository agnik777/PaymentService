# app/app.py
"""
candidate-service — платёжный сервис-посредник.

Точка входа FastAPI-приложения.
Собирает роутеры и управляет жизненным циклом, хранит singleton-объекты.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import config
from .database import create_tables, close_db
from .provider_client import ProviderClient
from .background import BackgroundProcessor
from .routers.health import router as health_router
from .routers.operations import router as operations_router

# ── Lifespan (startup / shutdown) ──────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Контекст жизненного цикла приложения.

    Startup:
      - Создаёт таблицы в БД.
      - Создаёт и сохраняет singleton-объекты (ProviderClient, BackgroundProcessor).
      - Запускает фоновый обработчик PROCESSING-операций.

    Shutdown:
      - Останавливает фоновый обработчик.
      - Закрывает HTTP-клиент провайдера.
      - Закрывает пул соединений с БД.
    """
    print("[lifespan] candidate-service запускается...")
    print(f"[lifespan] PROVIDER_URL = {config.PROVIDER_URL}")

    # Создаём таблицы (CREATE TABLE IF NOT EXISTS)
    print("[lifespan] Создание таблиц в БД...")
    await create_tables()
    print("[lifespan] Таблицы созданы (или уже существовали).")

    # Создание singleton-объектов и сохранение в app.state
    provider_client = ProviderClient()
    background_processor = BackgroundProcessor(provider_client)

    app.state.provider_client = provider_client
    app.state.background_processor = background_processor

    # Запуск фонового обработчика
    await background_processor.start()

    yield  # Приложение работает

    # Shutdown
    print("[lifespan] candidate-service завершает работу...")
    await background_processor.stop()
    await provider_client.close()
    await close_db()
    print("[lifespan] Ресурсы освобождены.")


app = FastAPI(
    title="candidate-service",
    description="Payment Service",
    version="0.0.1",
    lifespan=lifespan,
)


# Подключение роутеров
app.include_router(health_router)
app.include_router(operations_router)
