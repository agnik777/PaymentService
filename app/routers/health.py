# app/routers/health.py
"""
Роутер для проверки готовности сервиса.

Вынесен из main.py для единообразия структуры —
все эндпоинты живут в app/routers/.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.database import check_db_connection


router = APIRouter(tags=["health"])

@router.get("/health")
async def health():
    """
    Проверка готовности сервиса.

    Проверяет подключение к БД.
    200 — всё в порядке.
    503 — БД недоступна.
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
