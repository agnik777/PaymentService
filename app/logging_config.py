# app/logging_config.py
"""
Структурированное логирование.

Формат: JSON-строки с полями timestamp, level, logger, message и
опциональными параметрами operation_id, provider_payment_id, attempt.
"""

import logging
import json
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """
    Форматтер, выводящий логи в JSON.

    Каждая запись — одна строка JSON, что удобно для парсинга
    системами сбора логов (ELK, Loki и т.п.).
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Добавляем дополнительные поля, если они есть в record
        for attr in ("operation_id", "provider_payment_id", "attempt"):
            value = getattr(record, attr, None)
            if value is not None:
                log_entry[attr] = value

        # Если есть exception info — добавляем
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])

        return json.dumps(log_entry, ensure_ascii=False)

def setup_logging() -> None:
    """
    Настроить корневой логгер на вывод структурированных JSON-логов.

    Вызывается при старте приложения, до создания любых объектов.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    # Удаляем существующие обработчики, чтобы не дублировать
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Уменьшаем шум от библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

def get_logger(name: str) -> logging.Logger:
    """
    Получить логгер с заданным именем.

    Использование:
        logger = get_logger(__name__)
        logger.info("Сообщение", extra={"operation_id": "op-123"})
    """
    return logging.getLogger(name)
