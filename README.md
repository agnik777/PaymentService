```markdown
# candidate-service — платёжный сервис-посредник

Тестовое задание: сервис-посредник между клиентом и внешним платёжным провайдером.
Реализован на **Python / FastAPI / PostgreSQL**.

## Требования

- Docker и Docker Compose (версия 2.x)
- Свободные порты: `8080`, `8081`, `5432`

## Быстрый старт

```bash
# 1. Клонировать репозиторий
git clone <repo-url> && cd <project-dir>

# 2. Создать .env из шаблона (опционально — есть значения по умолчанию)
cp .env.example .env

# 3. Запустить все сервисы
docker compose up --build
```

После старта:

- **candidate-service** доступен на `http://localhost:8080`
- **provider-simulator** доступен на `http://localhost:8081`
- **PostgreSQL** доступен на `localhost:5432` (учётные данные: `candidate/candidate`, БД: `candidate`)

Проверка готовности:

```bash
curl -s http://localhost:8080/health | python3 -m json.tool
# → {"status": "ok", "database": "connected"}
```

## API

| Метод | Путь | Описание | Коды ответов |
|-------|------|----------|--------------|
| `POST` | `/operations` | Создать операцию | 201, 409, 422 |
| `GET` | `/operations/{id}` | Получить состояние | 200, 404 |
| `GET` | `/operations/{id}/events` | Получить историю событий | 200, 404 |
| `POST` | `/operations/{id}/submit` | Отправить провайдеру | 202, 200, 404 |
| `POST` | `/receipts` | Callback-квитанция (от провайдера) | 204, 404, 409 |
| `GET` | `/health` | Проверка готовности | 200, 503 |

## Архитектура

```
candidate-service (FastAPI, порт 8080)
├── POST /operations          — создание (→ CREATED)
├── GET  /operations/{id}     — чтение состояния
├── GET  /operations/{id}/events — история переходов
├── POST /operations/{id}/submit — отправка (CREATED → PROCESSING + вызов провайдера)
├── POST /receipts            — приём квитанции (→ COMPLETED / REJECTED)
└── Фоновый обработчик        — повторные вызовы провайдера с backoff

PostgreSQL (порт 5432)
├── operations      — платёжные операции
├── submit_intents  — намерения отправки (outbox)
└── events          — история переходов между статусами

provider-simulator (порт 8081)
├── POST /payments  — создание платежа
└── POST /receipts  — асинхронная callback-квитанция
```

### Диаграмма статусов

```
         POST /operations
               │
               ▼
          ┌─────────┐
          │ CREATED │
          └────┬────┘
               │ POST /operations/{id}/submit
               ▼
          ┌────────────┐
          │ PROCESSING │──────────────┐
          └─────┬──────┘              │
                │                     │
    POST /receipts (callback)         │ фон. обработчик
    ┌───────────┴───────────┐         │ (повторы)
    ▼                       ▼         │
┌──────────┐          ┌──────────┐    │
│COMPLETED │          │ REJECTED │◄───┘
└──────────┘          └──────────┘
```

### Принципы реализации

- **Атомарность**: все изменения в рамках одного эндпоинта — в одной транзакции.
- **Блокировка `SELECT ... FOR UPDATE`**: для синхронизации конкурентных `submit` и `receipts`.
- **Сохранение намерения до внешнего вызова**: запись в `submit_intents` внутри транзакции, HTTP-вызов провайдера — после коммита.
- **Идемпотентность**: `Idempotency-Key = operationId`, конкурентные submit не создают дубликатов.
- **Callback может прийти раньше ответа провайдера**: `provider_payment_id` устанавливается из первого пришедшего источника.
- **Финальный статус — только из квитанции**: HTTP-ответ провайдера (`202`) не меняет статус, только сохраняет `provider_payment_id`.

---

## Сквозной сценарий

Ниже — полный сквозной сценарий с curl-командами и ожидаемыми ответами.
Каждый шаг снабжён HTTP-кодом ответа (проверяется флагом `-w "\nHTTP: %{http_code}\n"`).

### 1. Проверка готовности

```bash
curl -s -w "\nHTTP: %{http_code}\n" http://localhost:8080/health

# Ожидаемый ответ (200):
# {"status":"ok","database":"connected"}
```

### 2. Создание операции

```bash
curl -s -w "\nHTTP: %{http_code}\n" \
  -X POST http://localhost:8080/operations \
  -H "Content-Type: application/json" \
  -d '{
    "operationId": "demo-001",
    "amount": "1500.00",
    "currency": "RUB",
    "description": "Демонстрационный платёж"
  }'

# Ожидаемый ответ (201):
# {
#   "operationId": "demo-001",
#   "amount": "1500.00",
#   "currency": "RUB",
#   "description": "Демонстрационный платёж",
#   "status": "CREATED",
#   "providerPaymentId": null,
#   "createdAt": "2026-08-06T...",
#   "updatedAt": "2026-08-06T..."
# }
```

### 3. Проверка дубликата

```bash
curl -s -w "\nHTTP: %{http_code}\n" \
  -X POST http://localhost:8080/operations \
  -H "Content-Type: application/json" \
  -d '{"operationId":"demo-001","amount":"999.99","currency":"RUB"}'

# Ожидаемый ответ (409):
# {"detail":"Операция с operationId='demo-001' уже существует"}
```

### 4. Валидация amount

```bash
# Отрицательное число
curl -s -w "\nHTTP: %{http_code}\n" \
  -X POST http://localhost:8080/operations \
  -H "Content-Type: application/json" \
  -d '{"operationId":"bad-1","amount":"-1.00","currency":"RUB"}'
# → 422

# Три знака после точки
curl -s -w "\nHTTP: %{http_code}\n" \
  -X POST http://localhost:8080/operations \
  -H "Content-Type: application/json" \
  -d '{"operationId":"bad-2","amount":"1.001","currency":"RUB"}'
# → 422

# Ноль
curl -s -w "\nHTTP: %{http_code}\n" \
  -X POST http://localhost:8080/operations \
  -H "Content-Type: application/json" \
  -d '{"operationId":"bad-3","amount":"0","currency":"RUB"}'
# → 422
```

### 5. Валидация currency

```bash
curl -s -w "\nHTTP: %{http_code}\n" \
  -X POST http://localhost:8080/operations \
  -H "Content-Type: application/json" \
  -d '{"operationId":"bad-4","amount":"100.00","currency":"USD"}'
# → 422
```

### 6. Получение состояния операции

```bash
curl -s -w "\nHTTP: %{http_code}\n" \
  http://localhost:8080/operations/demo-001

# Ожидаемый ответ (200):
# {... "status": "CREATED" ...}
```

### 7. Получение несуществующей операции

```bash
curl -s -w "\nHTTP: %{http_code}\n" \
  http://localhost:8080/operations/no-such-op
# → 404
```

### 8. История событий (пока одно событие CREATED)

```bash
curl -s -w "\nHTTP: %{http_code}\n" \
  http://localhost:8080/operations/demo-001/events

# Ожидаемый ответ (200):
# [
#   {
#     "eventId": 1,
#     "type": "CREATED",
#     "fromStatus": null,
#     "toStatus": "CREATED",
#     "message": "Operation created",
#     "occurredAt": "2026-08-06T..."
#   }
# ]
```

### 9. Отправка операции (submit)

```bash
curl -s -w "\nHTTP: %{http_code}\n" \
  -X POST http://localhost:8080/operations/demo-001/submit

# Ожидаемый ответ (202):
# {... "status": "PROCESSING" ...}
```

### 10. Повторный submit (идемпотентность)

```bash
curl -s -w "\nHTTP: %{http_code}\n" \
  -X POST http://localhost:8080/operations/demo-001/submit

# Ожидаемый ответ (200):
# {... "status": "PROCESSING" или "COMPLETED" ...}
```

### 11. Ожидание квитанции и проверка финального статуса

```bash
sleep 3

curl -s http://localhost:8080/operations/demo-001 | python3 -m json.tool

# Ожидаемый ответ (200):
# {... "status": "COMPLETED", "providerPaymentId": "..." ...}
```

### 12. Полная история после завершения

```bash
curl -s http://localhost:8080/operations/demo-001/events | python3 -m json.tool

# Ожидаемый ответ (200), минимум 3 события:
# [
#   {"eventId": 1, "type": "CREATED", ...},
#   {"eventId": 2, "type": "PROCESSING", ...},
#   {"eventId": 3, "type": "COMPLETED", ...}
# ]
```

### 13. Конкурентные submit

```bash
# Создать свежую операцию
curl -s -X POST http://localhost:8080/operations \
  -H "Content-Type: application/json" \
  -d '{"operationId":"race-test","amount":"50.00","currency":"RUB"}' > /dev/null

# Запустить 5 одновременных submit
for i in $(seq 1 5); do
  curl -s -o /dev/null -w "Запрос $i: HTTP %{http_code}\n" \
    -X POST http://localhost:8080/operations/race-test/submit &
done
wait

# Ожидаемый результат: один запрос получает 202, остальные — 200.
```

### 14. Конфликтующая квитанция (IGNORED_DUPLICATE_RECEIPT)

```bash
# Получить providerPaymentId демо-операции
PPID=$(curl -s http://localhost:8080/operations/demo-001 | python3 -c "import sys,json; print(json.load(sys.stdin)['providerPaymentId'])")

# Отправить REJECTED для уже COMPLETED операции
curl -s -w "\nHTTP: %{http_code}\n" -X POST \
  http://localhost:8080/receipts \
  -H "Content-Type: application/json" \
  -d "{
    \"providerPaymentId\": \"$PPID\",
    \"operationId\": \"demo-001\",
    \"result\": \"REJECTED\",
    \"message\": \"Поздний отказ\",
    \"occurredAt\": \"2026-08-06T12:00:00Z\"
  }"
# → 204

# Проверить, что статус НЕ изменился
curl -s http://localhost:8080/operations/demo-001 | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])"
# → COMPLETED

# Проверить, что событие IGNORED_DUPLICATE_RECEIPT появилось
curl -s http://localhost:8080/operations/demo-001/events | python3 -c "
import sys, json
events = json.load(sys.stdin)
ignored = [e for e in events if e['type'] == 'IGNORED_DUPLICATE_RECEIPT']
print(f'Найдено IGNORED_DUPLICATE_RECEIPT: {len(ignored)}')
print(f'fromStatus: {ignored[0][\"fromStatus\"]}, toStatus: {ignored[0][\"toStatus\"]}')
"
# → fromStatus: COMPLETED, toStatus: COMPLETED (статус не изменился)
```

### 15. Несовпадающий providerPaymentId (409)

```bash
curl -s -w "\nHTTP: %{http_code}\n" -X POST \
  http://localhost:8080/receipts \
  -H "Content-Type: application/json" \
  -d '{
    "providerPaymentId": "00000000-0000-0000-0000-000000000000",
    "operationId": "demo-001",
    "result": "COMPLETED",
    "message": "Чужой платёж",
    "occurredAt": "2026-08-06T12:00:00Z"
  }'
# → 409
```

### 16. Квитанция для несуществующей операции (404)

```bash
curl -s -w "\nHTTP: %{http_code}\n" -X POST \
  http://localhost:8080/receipts \
  -H "Content-Type: application/json" \
  -d '{
    "providerPaymentId": "aa5b7856-e9f2-4fd5-955b-38b1f28d9c57",
    "operationId": "nonexistent-op",
    "result": "COMPLETED",
    "message": "Нет такой операции",
    "occurredAt": "2026-08-06T12:00:00Z"
  }'
# → 404
```

### 17. Аудит провайдера (отсутствие дублирования платежей)

```bash
curl -s http://localhost:8081/audit | python3 -m json.tool | head -30

# Для каждого operationId должен быть ровно один платёж.
# Проверить можно так:
curl -s http://localhost:8081/audit | python3 -c "
import sys, json
from collections import Counter
audit = json.load(sys.stdin)
counts = Counter(item['operationId'] for item in audit)
duplicates = {k: v for k, v in counts.items() if v > 1}
if duplicates:
    print(f'НАЙДЕНЫ ДУБЛИКАТЫ: {duplicates}')
else:
    print('Дубликатов нет — идемпотентность работает')
"
```

### 18. Восстановление после перезапуска

```bash
# Создать операцию и отправить submit
curl -s -X POST http://localhost:8080/operations \
  -H "Content-Type: application/json" \
  -d '{"operationId":"restart-test","amount":"300.00","currency":"RUB"}' > /dev/null

curl -s -X POST http://localhost:8080/operations/restart-test/submit > /dev/null

# Подождать 1 секунду и остановить кандидат-сервис
sleep 1
docker compose stop candidate-service

# Проверить статус операции в БД
docker compose exec postgres psql -U candidate -d candidate \
  -c "SELECT operation_id, status, provider_payment_id FROM operations WHERE operation_id='restart-test';"

# Запустить заново
docker compose start candidate-service
sleep 5

# Проверить финальный статус
curl -s http://localhost:8080/operations/restart-test | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])"
# → COMPLETED (операция успешно восстановлена и завершена)
```

### 19. Проверка структурированных логов

```bash
docker compose logs candidate-service | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if line.startswith('{'):
        try:
            data = json.loads(line)
            op = data.get('operation_id', '-')
            lvl = data.get('level', '-')
            msg = data.get('message', '-')
            print(f'[{lvl}] [{op}] {msg}')
        except:
            pass
"
```

---

## Структура проекта

```
.
├── app/
│   ├── __init__.py               # Пакет
│   ├── app.py                   # Точка входа, lifespan
│   ├── config.py                 # Настройки (PROVIDER_URL, DATABASE_URL)
│   ├── database.py               # Подключение к БД, сессии
│   ├── models.py                 # SQLAlchemy-модели
│   ├── schemas.py                # Pydantic-схемы запросов/ответов
│   ├── dependencies.py           # FastAPI-зависимости
│   ├── provider_client.py        # HTTP-клиент для вызова провайдера
│   ├── background.py             # Фоновый обработчик PROCESSING-операций
│   ├── logging_config.py         # Структурированное JSON-логирование
│   └── routers/
│       ├── __init__.py
│       ├── health.py             # GET /health
│       ├── operations.py         # POST/GET /operations
│       └── receipts.py           # POST /receipts
├── .env.example                  # Шаблон переменных окружения
├── .env                          # Реальные настройки (в .gitignore)
├── .gitignore
├── .dockerignore
├── requirements.txt              # Зависимости Python
├── Dockerfile
├── compose.yaml                  # Docker Compose
└── README.md                     # Документация и сквозной сценарий
```

## Зависимости

| Пакет | Версия | Назначение |
|-------|--------|------------|
| fastapi | 0.115.6 | Веб-фреймворк |
| uvicorn | 0.34.0 | ASGI-сервер |
| sqlalchemy | 2.0.36 | ORM для PostgreSQL |
| asyncpg | 0.30.0 | Асинхронный драйвер PostgreSQL |
| python-dotenv | 1.0.1 | Загрузка .env |
| pydantic | 2.10.4 | Валидация данных |
| httpx | 0.28.1 | Асинхронный HTTP-клиент |

## Диагностика проблем

| Проблема | Решение |
|----------|---------|
| Порт 5432 занят | Остановить локальный PostgreSQL или сменить внешний порт в compose.yaml |
| Порт 8080 занят | Остановить процесс на порту или сменить в compose.yaml |
| `password authentication failed` | Проверить совпадение POSTGRES_USER/PASSWORD в compose.yaml и DATABASE_URL |
| Контейнер падает при старте | `docker compose logs candidate-service` |
| Таблицы не создаются | Проверить `docker compose logs candidate-service \| grep "Создание таблиц"` |

## Остановка

```bash
# Остановить контейнеры с сохранением данных
docker compose down

# Остановить и удалить все данные (полная очистка)
docker compose down -v
```