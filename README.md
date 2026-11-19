# content-factory

Файловый буфер готовых видео по языкам с плановым добором через движок TikTok.

Сервис считает остатки на диске, раз в несколько часов запускает производство
недостающего объёма и отдаёт готовые файлы партнёру по SFTP. Файловая система —
источник правды по остаткам, SQLite хранит только операционное состояние.

## Структура

```
src/content_factory/
  api/          # FastAPI, только localhost
  config/       # env-настройки и loguru
  constants/    # имена каталогов app_data
  exceptions/   # ошибки сервиса
  schemas/      # типы-данные
  services/     # доменные капсулы: поставщик, сток, алерты
  flows/        # Prefect flow stock_topup
app_data/       # рантайм, не в git
deploy/         # runbook Linux 1 и SFTP
scripts/        # бэкап конфига и запросов
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

## Деплой

Прод — Linux 1: `docker compose` поднимает `api` и Prefect-воркер, общий том
`./app_data` переживает пересоздание контейнеров, партнёр забирает файлы по
SFTP, конфиг и запросы уходят в ночной бэкап.

```bash
docker compose up -d --build
curl -f http://127.0.0.1:8000/api/v1/stock
```

Пошаговая инструкция (Prefect, SFTP, бэкап): [deploy/README.md](deploy/README.md).

## Настройки

Приложение читает только переменные окружения. Compose дополнительно
подхватывает `.env` из корня проекта для подстановки значений:

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `CONTENT_FACTORY_DATA_DIR` | `./app_data` | корень рантайм-данных |
| `CONTENT_FACTORY_LOG_LEVEL` | `INFO` | уровень loguru |
| `TELEGRAM_BOT_TOKEN` | — | токен бота для алертов |
| `TELEGRAM_CHAT_ID` | — | чат для алертов |

`app_data/` содержит `config/`, `db/`, `content/`, `queries/` и создаётся при
старте.
