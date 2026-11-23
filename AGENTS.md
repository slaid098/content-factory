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

## Проверки

Локально — targeted, без полного suite и coverage:

```bash
ruff check .
ruff format --check .
mypy src main.py
python -m pytest tests/<файл> -q --no-cov
```

Полный `python -m pytest` и coverage — в CI; `-n auto` локально запрещён.

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
