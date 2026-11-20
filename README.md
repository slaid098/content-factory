# content-factory

Файловый буфер готовых видео по языкам с плановым добором через движок TikTok.

Сервис считает остатки на диске, раз в несколько часов запускает производство
недостающего объёма и отдаёт готовые файлы партнёру по SFTP и HTTP-воркерам
по языку. Файловая система — источник правды по остаткам, SQLite хранит только
операционное состояние.

## Структура

```
src/content_factory/
  api/          # FastAPI, только localhost
  config/       # env-настройки и loguru
  constants/    # имена каталогов app_data
  exceptions/   # ошибки сервиса
  schemas/      # типы-данные
  services/     # доменные капсулы: поставщик, сток, алерты, выдача
  flows/        # Prefect flow stock_topup
app_data/       # рантайм, не в git
deploy/         # runbook Linux 1, GHCR и SFTP
docs/           # контракты API, включая выдачу контента
scripts/        # деплой и бэкап конфига и запросов
main.py         # ASGI-точка входа
```

## Разработка

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
pip install -e ".[dev]"

ruff check .
ruff format --check .
mypy src main.py
python -m pytest
```

## Запуск

```bash
uvicorn main:app --host 127.0.0.1 --port 8000
```

В контейнере команда переопределяется на `0.0.0.0:8000`, но порт публикуется
только на loopback хоста (`127.0.0.1:8000:8000`). Без compose образ по
умолчанию слушает `127.0.0.1`.

## Выдача контента

Воркеры забирают готовые видео по языку через HTTP, не читая SFTP-каталоги:

```bash
curl -f -X POST http://127.0.0.1:8000/api/v1/content/deliveries \
  -H "Authorization: Bearer $CONTENT_FACTORY_DELIVERY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"language":"ru","count":2,"request_id":"3f1c1b0e-6f1a-4f6e-9d2e-0f6a7b8c9d10"}' \
  -o delivery.zip
```

Тело запроса: `language` (`^[a-z]{2}$`), `count` (1–300, фактический
максимум — `delivery.max_count` в runtime-конфиге, по умолчанию 100),
`request_id` (UUID), необязательный `query` (тема, до 200 символов). Ответ
`200` — zip с `NNN.mp4` и `NNN.json` (sidecar `{"title": "..."}`); `204` — в
языке или выбранной теме нет `.mp4`; `401` — нет или неверный Bearer-токен;
`422` — невалидное тело или `count` выше лимита; `503` — токен не настроен.

Для ручной выдачи сотен видео есть асинхронный
`POST /api/v1/content/deliveries/link`: он отвечает `202`, собирает zip в фоне,
заливает его на Litterbox на 24 часа и оставляет на сервере sidecar со ссылкой.
Статус — `GET /api/v1/content/deliveries/<language>/<request_id>`.

Остаток по темам отдаёт `GET /api/v1/content/topics?language=ru`, список
живых архивов — `GET /api/v1/content/archives`, повторное скачивание —
`GET /api/v1/content/archives/<language>/<request_id>.zip`. Выданные `.mp4`
удаляются из `content/<language>/`, zip хранится в
`app_data/archive/<language>/<request_id>.zip`. Повтор с тем же `request_id`
в пределах TTL (по умолчанию 24 часа) возвращает тот же архив. Старые архивы
удаляет APScheduler раз в час. Полный контракт:
[docs/content-delivery.md](docs/content-delivery.md).

## Деплой

Прод — Linux 1: `docker compose` поднимает `api` и Prefect-воркер, общий том
`./app_data` переживает пересоздание контейнеров, партнёр забирает файлы по
SFTP, конфиг и запросы уходят в ночной бэкап. Образ собирается в CI и
публикуется в GHCR (`ghcr.io/slaid098/content-factory`), деплой на Linux 1
идёт автоматически после зелёного `main`.

```bash
docker compose pull
docker compose up -d
curl -f http://127.0.0.1:8000/api/v1/stock
```

Для локальной разработки образ собирается на месте:
`docker compose up -d --build`.

Перед первым production-запуском создайте пользователя и каталоги языков:
приложение не может писать в root-овый `content/`. Пошаговая инструкция
(каталоги, Prefect, SFTP, бэкап): [deploy/README.md](deploy/README.md).

## Настройки

Приложение читает только переменные окружения. Compose дополнительно
подхватывает `.env` из корня проекта для подстановки значений:

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `CONTENT_FACTORY_DATA_DIR` | `./app_data` | корень рантайм-данных |
| `CONTENT_FACTORY_LOG_LEVEL` | `INFO` | уровень loguru |
| `CONTENT_FACTORY_DELIVERY_TOKEN` | — | Bearer-токен выдачи; пусто — выдача выключена |
| `CONTENT_FACTORY_DELIVERY_RETENTION_HOURS` | `24` | TTL zip-архивов, часов (≥ 1) |
| `TELEGRAM_BOT_TOKEN` | — | токен бота для алертов |
| `TELEGRAM_CHAT_ID` | — | чат для алертов |

`app_data/` содержит `config/`, `db/`, `content/`, `archive/`, `queries/` и
создаётся при старте.
