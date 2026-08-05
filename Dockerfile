# Dockerfile

# Базовый образ Python 3.14 (slim — минимальный размер)
FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Отключаем буферизацию stdout/stderr, чтобы логи были видны сразу
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Открываем порт 8080 (декларативно, для документации)
EXPOSE 8080

# Команда запуска: uvicorn на 0.0.0.0:8080
CMD ["uvicorn", "app.app:app", "--host", "0.0.0.0", "--port", "8080"]
