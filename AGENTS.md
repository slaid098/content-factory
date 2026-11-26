# content-factory

Профиль проекта к глобальному `AGENTS.md`; нормативный стандарт кода —
скилл `code-standards`.

## Стек и раскладка

- Python 3.12, пакет в `src/content_factory/` (FastAPI, Prefect, SQLite).
- Тесты — `tests/`; полный suite с coverage запускает только CI.
- Конфиг — env-only (`CONTENT_FACTORY_*`, `TITLES_*`), runtime-настройки —
  `app_data/config/config.yaml` через API.
- Ключ и адрес LLM одни на проект (`OPENAI_API_KEY`, `OPENAI_BASE_URL`),
  модель своя на задачу (`TITLES_MODEL`, `GENERATOR_MODEL`).
- Отбор видео — продуктовый инвариант скилла `content-selection`; фильтры
  поставщика и `SUPPLIER_*` без задачи не менять.

## Продукт: два режима

Фабрика — многоуровневая система производства контента: ролики из TikTok,
генерация через Vast.ai и другие способы, позже статьи и скрипты. Готовый
контент выдаётся строго в двух режимах, и они не смешиваются.

Разрешённый адрес выдачи всегда содержит язык: в General его задаёт запрос, в
Profile он берётся из профиля. Явный `language` профильного запроса должен
совпадать с языком профиля, иначе `422`; подменить профиль на General нельзя.

- **General** — язык без профиля: контент без профиля (ролики из TikTok и
  другой общий контент), ниша `general`, `profile_id` пуст. `language`
  обязателен.
- **Profile** — профиль, например `{"profile":"dating/profile_1"}`; язык
  берётся из реестра профиля и указывать его не обязательно. Идентификатор
  профиля уже содержит тему (`dating`), `profile_id` заполнен.

General-запрос никогда не получает профильный сток, профильный запрос —
General. Новые виды контента (статьи, скрипты) встраиваются в эти же два
режима, а не заводят третий.

Тип контента — `video` или `photo` — измерение профиля, а не отдельный
профиль: один профиль даёт и видео, и фото. General всегда видео по языку,
`type=photo` без профиля отклоняется. Один архив выдачи содержит один тип.

Это правило продукта, а не временное состояние: не описывай пустой
профильный сток как дефект General и не предлагай третий режим.

## Проверки

Локально — targeted, без полного suite и coverage:

```bash
ruff check .
ruff format --check .
mypy src main.py
python -m pytest tests/<файл> -q --no-cov
```

UI — Vite + TypeScript в `frontend/`; сборка кладёт бандл в
`src/content_factory/api/ui` и нужна перед Python-тестами страницы:

```bash
cd frontend
npm ci
npm run build
npm run lint
npm run typecheck
npm run test
```

Полный `python -m pytest` и coverage — в CI; `-n auto` локально запрещён.
Playwright (`npm run test:e2e`) гоняется в CI; локально — только если
установлен Chromium.

## Доступ

Партнёр попадает на `content.slaid098.dev` по одноразовой ссылке на
устройство, а не по IP-списку NPM. Ссылки выпускает CLI внутри контейнера
`api`:

```bash
cd /root/dockers/content-factory
docker compose exec -T api python scripts/device_access.py issue --label phone
docker compose exec -T api python scripts/device_access.py list
docker compose exec -T api python scripts/device_access.py revoke --id <id>
```

Ссылка одноразовая: после первого открытия URL мёртв, а cookie
`device_token` host-only живёт до `expires_at` устройства. Без действующей
cookie API отвечает JSON 403, страницы — HTML 403. Выключить gate можно
только для отладки: `CONTENT_FACTORY_DEVICE_GATE_ENABLED=false`; тесты
отключают его фикстурой `tests/conftest.py`. Подробности — `deploy/README.md`
§12.

## Генератор

- Аренда серверов: поднимать сразу несколько инстансов (например, три) и
  убивать те, что поднимаются дольше всех. Пока образ запечён, простой при
  подъёме стоит копейки, а первый готовый сервер экономит десятки минут на
  загрузке образа. Ту же механику закладывать в автоматическую аренду, когда
  партии нужно несколько серверов.
- Сервер поднялся, но ComfyUI не отвечает или задание висит без результата —
  не ждать: проверить статус и сразу погасить инстанс. Признак живого сервера
  только ответ `/system_stats` и результат задания, а не «инстанс создан»:
  пока он «поднимается», деньги уже капают.
- Рецепт `production` в `profile.yaml` задаёт producer, тип и QA шага; без
  блока действует прежний `vast/video/[media, clef]`, фото-профиль объявляет
  `card/photo/[image]`. QA по умолчанию зависит от типа, чужие проверки
  (`media`/`clef` в photo, `image` в video) — `ProfileError`. `dry_run`
  строит план по каждому шагу рецепта, не вызывая producers. Новый способ
  генерации подключается через реестр producers и рецепт, а не правкой
  `generate_batch`. Подробности — `docs/generator-profiles.md`.

## Аудит и merge-гейт

`audit` — WARN-only и не блокирует сборку. Единственный блокирующий
аудит-шаг — `audit-gate`: он падает только на новых находках в файлах,
изменённых против базового коммита. Версия движка закреплена тегом
(`v0.6.0`), не `main`.

Локальный запуск повторяет CI:

```bash
ENGINE='git+https://git.slaid098.dev/slaid098/agent-skills@v0.6.0'

# движок закрытый: без авторизации uvx по HTTPS получит 401. Один раз
# настройте git с токеном read:repository из ENGINE_GIT_TOKEN…
git config --global url."https://oauth2:${ENGINE_GIT_TOKEN}@git.slaid098.dev/".insteadOf "https://git.slaid098.dev/"
# …или подставьте токен прямо в ENGINE:
# ENGINE='git+https://oauth2:${ENGINE_GIT_TOKEN}@git.slaid098.dev/slaid098/agent-skills@v0.6.0'

git fetch origin main
base=$(git merge-base HEAD origin/main)

git worktree add --detach /tmp/cf-audit-base "$base"
uvx --python 3.12 --from "$ENGINE" audit --repo /tmp/cf-audit-base --json-report > /tmp/audit-baseline.json
git worktree remove --force /tmp/cf-audit-base

uvx --python 3.12 --from "$ENGINE" audit-gate --repo . \
  --baseline /tmp/audit-baseline.json --base "$base" --scope changed
```

Гейт видит только закоммиченное: закоммить изменения перед запуском, иначе
незакоммиченные правки в diff не попадут.

Baseline — schema 3, в репозиторий не коммитится: CI хранит его в
`$RUNNER_TEMP` и кэше по SHA базового коммита. Движок ставится по HTTPS из
закрытого репозитория: в CI — с секретом `ENGINE_GIT_TOKEN` с доступом на
чтение к репозиторию движка, для PR из форков job пропускается (GitHub не
отдаёт форкам секреты). Ложные срабатывания гасятся `[tool.audit]` в
`pyproject.toml` с причиной; гейт не блокирует существующие находки.
