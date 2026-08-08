# app/config.py

"""
Централизованные настройки приложения.
Все параметры читаются из переменных окружения. Это позволяет
переопределять их в .env без изменения кода.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    POSTGRES_USER = os.getenv("POSTGRES_USER")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
    POSTGRES_DB = os.getenv("POSTGRES_DB")
    POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
    POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")

    # URL для подключения к PostgreSQL
    DATABASE_URL: str = (f"postgresql+asyncpg://"
                    f"{POSTGRES_USER}:{POSTGRES_PASSWORD}@"
                    f"{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}")

    # URL внешнего провайдера (симулятора)
    PROVIDER_URL: str = os.getenv("PROVIDER_URL", "http://provider-simulator:8081")


config = Config()
