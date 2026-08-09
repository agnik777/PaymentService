# app/metrics.py
"""
Prometheus-метрики для мониторинга candidate-service.

Экспортируются через эндпоинт GET /metrics в формате Prometheus text.

Метрики:
  - candidate_operations_total: счётчик операций по статусу
  - candidate_submit_attempts_total: счётчик попыток отправки
  - candidate_receipts_total: счётчик квитанций по результату
  - candidate_processing_gauge: количество незавершённых операций
  - candidate_http_request_duration_seconds: гистограмма длительности запросов
"""

from prometheus_client import Counter, Gauge, Histogram, generate_latest, REGISTRY
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse


# ── Счётчики ───────────────────────────────────────────────────────────────

# Количество операций, созданных с каждым статусом
operations_created = Counter(
    "candidate_operations_total",
    "Total number of operations created",
    ["status"],
)

# Количество попыток отправки (submit) — успешных и нет
submit_attempts = Counter(
    "candidate_submit_attempts_total",
    "Total number of submit attempts",
    ["result"],  # "accepted", "already_processing", "already_final", "not_found"
)

# Количество полученных квитанций
receipts_processed = Counter(
    "candidate_receipts_total",
    "Total number of receipts processed",
    ["result"],  # "completed", "rejected", "ignored_duplicate", "conflict", "not_found"
)

# ── Gauge (текущее значение) ───────────────────────────────────────────────

# Количество операций в статусе PROCESSING (незавершённых)
processing_gauge = Gauge(
    "candidate_operations_processing",
    "Current number of operations in PROCESSING status",
)

# ── Гистограмма ────────────────────────────────────────────────────────────

# Длительность HTTP-запросов
http_request_duration = Histogram(
    "candidate_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0],
)

# ── Middleware для замера длительности ─────────────────────────────────────

from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request
import time


class MetricsMiddleware(BaseHTTPMiddleware):
    """
    Middleware, замеряющий длительность каждого HTTP-запроса.

    Устанавливается на всё приложение. Не влияет на бизнес-логику.
    """

    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start

        # Не пишем метрику для /metrics (рекурсия)
        if request.url.path != "/metrics":
            http_request_duration.labels(
                method=request.method,
                endpoint=request.url.path,
            ).observe(duration)

        return response

# ── Роутер /metrics ────────────────────────────────────────────────────────

metrics_router = APIRouter(tags=["metrics"])

@metrics_router.get("/metrics")
async def get_metrics():
    """
    Эндпоинт для сбора метрик Prometheus.

    Возвращает текстовый формат Prometheus.
    Не кэшируется, всегда актуальные значения.
    """
    return PlainTextResponse(
        content=generate_latest(REGISTRY),
        media_type="text/plain; version=0.0.4",
    )
