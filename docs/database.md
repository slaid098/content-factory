# База данных

Операционное состояние сервиса — сток, названия, настройки профилей, состояние
цикла и решения поставщика — хранится в одной базе. Бэкендов два: SQLite для
разработки и тестов и PostgreSQL для прода. Выбор делает переменная
`CONTENT_FACTORY_DATABASE_URL`.

## Выбор бэкенда

| `CONTENT_FACTORY_DATABASE_URL` | Бэкенд | Адрес |
|---|---|---|
| пусто или не задана | SQLite | `app_data/db/db.sqlite3` |
| `postgresql://…` или `postgres://…` | PostgreSQL | DSN |

DSN с другой схемой отклоняется на старте с `ConfigError`: молча
переключиться на SQLite нельзя. В режиме PostgreSQL путь к SQLite-файлу не
создаётся, не проверяется и не читается.

DSN должен явно указывать хост и имя базы, а пароль — только в userinfo:
`postgresql://user:password@host:5432/db`. В query разрешены только
`sslmode`, `application_name` и `connect_timeout`; остальные параметры
(включая `password`, `sslpassword` и их percent-encoded формы) и фрагмент
`#` отклоняются. `ConfigError` на старте не повторяет значение DSN.

Продукт, выдача, формат стока и архива, API и `run.json` от бэкенда не
зависят.

## Соединения

`content_factory.db.connection` — единственное место, которое импортирует
драйверы (`sqlite3`, `psycopg`); импортёр SQLite — исключение. Наружу отдаётся
один порт: `execute`, `executemany`, `commit`, `rollback`, `close`,
контекстный менеджер, `rowcount` и строки, доступные и по имени
(`row["col"]`), и по позиции (`row[0]`).

SQL во всём сервисе пишется с плейсхолдерами `?`; адаптер PostgreSQL заменяет
их на `%s`. В строковых литералах SQL знак `?` не встречается — это
зафиксировано тестом.

| Настройка | SQLite | PostgreSQL |
|---|---|---|
| журнал | `journal_mode=WAL` | — |
| ожидание блокировки | `busy_timeout=30000` | `lock_timeout=30s` |
| внешние ключи | `foreign_keys=ON` | всегда включены |
| таймаут запроса | — | не задаётся |
| автокоммит | — | выключен |

Ошибки драйверов переводятся в `DatabaseError`, `IntegrityError` и
`OperationalError` из `content_factory.db.errors`; сервис ловит только их.

## Схема

Операционных таблиц девять: `stock_items`, `titles`, `profile_settings`,
`runtime_state`, `alert_cooldowns`, `videos`, `downloads`, `source_items`,
`source_cursors`. Схема описана миграциями
`db/migrations/0001_initial.py` и `db/migrations/0002_sources.py` и включает:

- `stock_items.content_type` (`video`/`photo`) и индекс по языку, типу и
  выдаче;
- `videos.language` и частичный UNIQUE `videos(author) WHERE status =
  'downloaded'`: у одного автора не больше одного скачанного видео;
- `downloads` с внешним ключом на `videos`;
- `source_items` — метаданные исходников уникализатора: путь, язык, профиль,
  SHA-256, размер, длительность, `added_at`, `used_at`, `missing_at`,
  `use_count`; UNIQUE `(language, profile_id, sha256)`;
- `source_cursors` — последний обработанный `last_path` профиля, PK
  `(language, profile_id)`.

Служебная `schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT
NULL)` создаётся раннером и в число операционных таблиц не входит.

## Миграции

Каталог `src/content_factory/db/migrations/`; версии только вперёд, по одной
транзакции на миграцию. Раннер `migrations/runner.py`:

1. создаёт `schema_migrations`, если её нет;
2. читает применённые версии;
3. применяет отсутствующие по порядку и записывает версию.

Два процесса не применяют одну миграцию дважды: на PostgreSQL берётся
`pg_advisory_lock`, на SQLite — существующий файловый lock
`app_data/db/migrations.lock`. Второй процесс ждёт и видит уже применённые
версии.

Миграции применяются один раз на процесс при первом `connect()`, поэтому API и
воркер стартуют независимо. Вручную:

```bash
python -m content_factory.db.migrate
```

Команда идемпотентна и печатает применённые версии. Новая миграция — модуль
`0003_<имя>.py` с `VERSION` и `apply(connection)`, добавленный в
`MIGRATION_MODULES`; менять уже применённую миграцию нельзя.

## Импорт SQLite → PostgreSQL

Перед переключением прода перенесите состояние одной командой:

```bash
python -m content_factory.db.import_sqlite --sqlite app_data/db/db.sqlite3
```

Импорт читает семь операционных таблиц стока, названий и настроек, пишет в
текущую базу (DSN) и печатает счётчики по таблицам. Метаданные
`source_items`/`source_cursors` он не переносит: источники уникализатора
восстанавливаются импортом папок, а курсоры начинаются заново. Вставки идут с
`ON CONFLICT DO NOTHING`, поэтому повторный запуск не создаёт дублей и не
затирает уже перенесённые строки; `delivered_at` и `request_id` стока
сохраняются. Весь импорт — одна транзакция: при ошибке чтения или записи база
остаётся нетронутой, команда завершается ненулевым кодом. Файлы контента
импорт не трогает, `stock_items` можно пересобрать сканом.

## Откат

1. Остановите API и воркер.
2. Уберите `CONTENT_FACTORY_DATABASE_URL` из `.env` (пустое значение).
3. Поднимите сервисы с прежним `app_data/db/db.sqlite3`.

SQLite-файл при работе на PostgreSQL не меняется, поэтому откат возвращает
состояние на момент переключения. Записи, сделанные после переключения, в нём
отсутствуют — при необходимости переносите их отдельно.

## Границы

- Сервис ходит только в свою базу `content_factory`; таблицы YouTube Kit не
  читаются и не меняются.
- Файловые блокировки `db/lock.py` остаются как есть и не переводятся в
  advisory locks.
- Пул соединений не вводится: соединение на операцию, как и раньше.

## Проверка

PostgreSQL-набор включается переменной окружения и без неё пропускается:

```bash
CONTENT_FACTORY_TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/content_factory_test \
  python -m pytest tests/integration/db -q --no-cov
```

CI поднимает `postgres:17`, создаёт базу `content_factory_test` и задаёт эту
переменную; полный pytest проверяет оба бэкенда.
