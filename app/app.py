# app/app.py
"""
candidate-service — платёжный сервис-посредник.

Точка входа FastAPI-приложения.
Собирает роутеры, управляет жизненным циклом, хранит singleton-объекты,
обрабатывает сигналы ОС для graceful shutdown.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import config
from .database import create_tables, close_db
from .provider_client import ProviderClient
from .background import BackgroundProcessor
from .logging_config import setup_logging, get_logger
from .routers.health import router as health_router
from .routers.operations import router as operations_router
from .routers.receipts import router as receipts_router
from .metrics import metrics_router, MetricsMiddleware


logger = get_logger(__name__)

# ── Lifespan (startup / shutdown) ──────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Контекст жизненного цикла приложения.

    Startup:
      - Настраивает структурированное логирование.
      - Создаёт таблицы в БД.
      - Создаёт singleton-объекты.
      - Запускает фоновый обработчик (с восстановлением PROCESSING-операций).

    Shutdown:
      - Корректно останавливает фоновый обработчик.
      - Закрывает HTTP-клиент и пул БД.
    """

    setup_logging()

    logger.info("candidate-service запускается...")
    logger.info("PROVIDER_URL=%s", config.PROVIDER_URL)

    # Создаём таблицы (CREATE TABLE IF NOT EXISTS)
    logger.info("Создание таблиц в БД...")
    await create_tables()
    logger.info("Таблицы созданы (или уже существовали)")

    # Создание singleton-объектов и сохранение в app.state
    provider_client = ProviderClient()
    background_processor = BackgroundProcessor(provider_client)

    app.state.provider_client = provider_client
    app.state.background_processor = background_processor

    # Запуск фонового обработчика (включает _recover_operations)
    await background_processor.start()

    yield  # Приложение работает

    # Shutdown
    logger.info("candidate-service завершает работу...")
    await background_processor.stop()
    await provider_client.close()
    await close_db()
    logger.info("Все ресурсы освобождены")


app = FastAPI(
    title="candidate-service",
    description="Payment Service",
    version="0.0.1",
    lifespan=lifespan,
)


# Middleware для метрик (замер длительности запросов)
app.add_middleware(MetricsMiddleware)

# Подключение роутеров
app.include_router(health_router)
app.include_router(operations_router)
app.include_router(receipts_router)
app.include_router(metrics_router)
