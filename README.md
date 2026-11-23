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
  db/           # SQLite-доступ и SQL слоя БД
  exceptions/   # ошибки сервиса
  schemas/      # типы-данные
  services/     # доменные капсулы: поставщик, сток, алерты, выдача
  flows/        # Prefect flows: stock_topup и titles
app_data/       # рантайм, не в git
deploy/         # runbook Linux 1, GHCR и SFTP
docs/           # контракты API, выдачи и названий
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
python -m pytest tests/unit -q --no-cov
```

### Аудит

`audit` — WARN-only и ничего не блокирует. Блокирует только `audit-gate`,
и только новыми находками в файлах, изменённых против базового коммита.
Версия движка закреплена тегом `v0.5.0`; локальный запуск повторяет CI:

```bash
ENGINE='git+https://git.slaid098.dev/slaid098/agent-skills@v0.5.0'

# движок закрытый: без авторизации uvx по HTTPS получит 401. Один раз
# настройте git с токеном read:repository из ENGINE_GIT_TOKEN…
git config --global url."https://oauth2:${ENGINE_GIT_TOKEN}@git.slaid098.dev/".insteadOf "https://git.slaid098.dev/"
# …или подставьте токен прямо в ENGINE:
# ENGINE='git+https://oauth2:${ENGINE_GIT_TOKEN}@git.slaid098.dev/slaid098/agent-skills@v0.5.0'

git fetch origin main
base=$(git merge-base HEAD origin/main)

# baseline на базовом коммите — в CI он кэшируется по SHA
git worktree add --detach /tmp/cf-audit-base "$base"
uvx --python 3.12 --from "$ENGINE" audit --repo /tmp/cf-audit-base --json-report > /tmp/audit-baseline.json
git worktree remove --force /tmp/cf-audit-base

# гейт: только новые находки в изменённых файлах
uvx --python 3.12 --from "$ENGINE" audit-gate --repo . \
  --baseline /tmp/audit-baseline.json --base "$base" --scope changed
```

Гейт видит только закоммиченное: закоммить изменения перед запуском, иначе
незакоммиченные правки в diff не попадут.

Baseline — отчёт schema 3 и в репозиторий не коммитится: CI держит его в
`$RUNNER_TEMP` и кэше по SHA базового коммита. Движок ставится по HTTPS из
закрытого репозитория; в CI для этого используется read-only секрет
`ENGINE_GIT_TOKEN`, а для PR из форков job пропускается — GitHub не отдаёт
форкам секреты.

## Запуск

```bash
uvicorn main:app --host 127.0.0.1 --port 8000
```

В контейнере команда переопределяется на `0.0.0.0:8000`, но порт публикуется
только на loopback хоста (`127.0.0.1:8000:8000`). Без compose образ по
умолчанию слушает `127.0.0.1`.

## Выдача контента

Ручная выдача идёт через веб-страницу `GET /` с вкладками «Выдача»,
«Запросы» и «Настройки». На вкладке «Выдача» выбирают язык и количество
видео, страница запускает асинхронную сборку, опрашивает её статус и
показывает готовую ссылку; темы стока выводятся только для чтения. «Запросы»
показывают список с остатком и позволяют добавить новый запрос, «Настройки» —
проверить diff и применить runtime-конфиг с бэкапом. Лимит одного архива —
`delivery.max_count` из настроек, по умолчанию 100, максимум 300; страница
берёт его из `GET /api/v1/content/settings` и ограничивает остатком языка.

Воркеры забирают готовые видео по языку через HTTP, не читая SFTP-каталоги:

```bash
curl -f -X POST http://127.0.0.1:8000/api/v1/content/deliveries \
  -H "Authorization: Bearer $CONTENT_FACTORY_DELIVERY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"language":"ru","count":2,"request_id":"3f1c1b0e-6f1a-4f6e-9d2e-0f6a7b8c9d10"}' \
  -o delivery.zip
```

Тело запроса: `language` (`^[a-z]{2,3}$`), `count` (1–300, фактический
максимум — `delivery.max_count` в runtime-конфиге, по умолчанию 100),
`request_id` (UUID), необязательный `query` (тема, до 200 символов). Ответ
`200` — zip с `NNN.mp4` и `NNN.json` (sidecar `{"title": "..."}`, название из
`titles`, а не из описания TikTok); `204` — в языке или выбранной теме нет
названных видео, даже если `.mp4` есть; `401` — нет или неверный Bearer-токен;
`422` — невалидное тело или `count` выше лимита; `503` — токен не настроен.

Для ручной выдачи сотен видео есть асинхронный
`POST /api/v1/content/deliveries/link`: он отвечает `202`, собирает zip в фоне,
заливает его на Filebin, а на сервере на 24 часа оставляет sidecar со ссылкой.
Статус — `GET /api/v1/content/deliveries/<language>/<request_id>`. Страница
опрашивает его раз в 3 секунды: `ready` даёт внешнюю ссылку, `ready_local` —
локальный архив, `empty` — «нет названных видео», `409` — «сборка уже идёт».
Скачивание идёт обычным переходом по ссылке, без `fetch` и blob.

Остаток по темам отдаёт `GET /api/v1/content/topics?language=ru`, список
живых архивов — `GET /api/v1/content/archives`, повторное скачивание —
`GET /api/v1/content/archives/<language>/<request_id>.zip`. Выданные `.mp4`
удаляются из `content/<language>/`, zip хранится в
`app_data/archive/<language>/<request_id>.zip`. Повтор с тем же `request_id`
в пределах TTL (по умолчанию 24 часа) возвращает тот же архив. Старые архивы
удаляет APScheduler раз в час. Полные контракты:
[docs/content-delivery.md](docs/content-delivery.md),
[docs/content-queries.md](docs/content-queries.md),
[docs/runtime-settings.md](docs/runtime-settings.md).

## Названия

Flow `titles` раз в 10 минут запрашивает для стоковых видео без названия три
варианта по кадрам через DeepSeek и сохраняет первый уникальный. Названия
лежат в таблице `titles` операционной базы, язык берётся из каталога
`content/<lang>`, уже названные файлы не перезаписываются. Без
`TITLES_API_KEY` flow выходит со статусом `disabled` и не падает. Контракт,
расписание и первый прогон: [docs/titles.md](docs/titles.md).

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

Для локальной разработки образ собирается на месте из
`docker/prod/Dockerfile`: `docker compose up -d --build`.

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
| `TITLES_API_URL` | `https://ollama.com/v1` | база API названий; `http`/`https`, без пробелов и завершающего `/` |
| `TITLES_API_KEY` | — | ключ API названий; пусто — flow `titles` выключен |
| `TITLES_MODEL` | `deepseek-v4.1-flash` | модель названий |
| `TITLES_TIMEOUT_SECONDS` | `60` | таймаут запроса названий, 5–300 |
| `TITLES_REASONING_EFFORT` | `none` | `none`, `low`, `high` или `max` |
| `TELEGRAM_BOT_TOKEN` | — | токен бота для алертов |
| `TELEGRAM_CHAT_ID` | — | чат для алертов |

`app_data/` содержит `config/`, `db/`, `content/`, `archive/`, `queries/` и
создаётся при старте.

Параметры производства лежат в `app_data/config/config.yaml` и правятся через
API с preview, бэкапом и сохранением комментариев:
[docs/runtime-settings.md](docs/runtime-settings.md).
