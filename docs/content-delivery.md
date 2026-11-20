# Выдача контента

HTTP-контракт для воркеров, которым нужны готовые видео по языку без доступа
к SFTP-каталогам. Сервис сам выбирает файлы из стока, упаковывает их в zip и
удаляет выданные `.mp4` из `content/<language>/`.

## Endpoint

```
POST /api/v1/content/deliveries
```

Тело запроса:

```json
{"language": "ru", "count": 1, "request_id": "3f1c1b0e-6f1a-4f6e-9d2e-0f6a7b8c9d10"}
```

| Поле | Правило |
|---|---|
| `language` | строка по regex `^[a-z]{2}$` |
| `count` | целое от 1 до 10 |
| `request_id` | UUID |
| `query` | необязательная тема, строка от 1 до 200 символов |

Если `query` не передан, выбираются файлы всех тем. Если передан — только
файлы, у которых `videos.query` совпадает точно, с учётом регистра.

## Авторизация

Заголовок `Authorization: Bearer <CONTENT_FACTORY_DELIVERY_TOKEN>`.

- Токен не задан в окружении — `503`, выдача выключена.
- Токен неверный или заголовка нет — `401`.
- Токен не логируется и не попадает в ответы.

## Ответы

| Код | Когда | Тело |
|---|---|---|
| `200` | есть хотя бы один `.mp4` | `application/zip`, `Content-Disposition: attachment; filename="delivery-<request_id>.zip"` |
| `204` | в `content/<language>/` нет `.mp4` | пусто |
| `401` | нет заголовка или токен неверный | JSON с ошибкой |
| `422` | тело не прошло валидацию | JSON с ошибкой |
| `500` | не удалось собрать или записать zip | JSON с ошибкой |
| `503` | `CONTENT_FACTORY_DELIVERY_TOKEN` не задан | JSON с ошибкой |

Если файлов меньше, чем `count`, отдаются все имеющиеся.

## Темы стока

```
GET /api/v1/content/topics?language=ru
```

Ответ — остаток `.mp4` по темам для одного языка:

```json
{
  "language": "ru",
  "topics": [{"query": "заправка", "count": 3}],
  "unclassified": 2
}
```

`topics` содержит только темы с `count > 0` и отсортирован по `query`.
`unclassified` — файлы без строки в `downloads`/`videos` или с пустым
`videos.query`.

## Формат архива

Внутри zip — пары файлов с трёхзначным номером:

```
001.mp4
001.json
002.mp4
002.json
```

`NNN.json` — sidecar YouTube Kit:

```json
{"title": "Заголовок видео"}
```

Пишется только `title`. Хэштеги в sidecar не попадают, чтобы не отключать
хэштеги канала; `made_for_kids` не пишется.

### Заголовок

`title` берётся из `videos.description` (TikTok desc) по SHA-256 имени файла:
`downloads.sha256 → downloads.video_id → videos.description`.

- пробелы и переводы строк нормализуются в один пробел;
- хэштеги (`#…`) удаляются;
- длина — не больше 100 символов, при обрезке добавляется `…`;
- если строки нет, описание пустое или БД недоступна — fallback:
  `Видео` для `ru`, `Video` для остальных языков.

## Хранение и TTL

Архив хранится в:

```
app_data/archive/<language>/<request_id>.zip
```

Исходные `.mp4` удаляются только после успешной записи zip. Если удаление
файла не удалось, ошибка логируется, а zip всё равно отдаётся.

TTL задаётся `CONTENT_FACTORY_DELIVERY_RETENTION_HOURS` (по умолчанию `24`,
минимум `1`).

## Список и скачивание архивов

```
GET /api/v1/content/archives
```

Возвращает массив живых архивов, новые сверху:

```json
[
  {
    "language": "ru",
    "request_id": "3f1c1b0e-6f1a-4f6e-9d2e-0f6a7b8c9d10",
    "size": 123456,
    "created_at": "2026-05-01T12:00:00Z",
    "expires_at": "2026-05-02T12:00:00Z"
  }
]
```

`created_at` берётся из mtime файла, `expires_at = created_at + TTL`. Архивы
старше TTL не показываются, даже если cleanup ещё не успел их удалить.
Файлы `.tmp` и имена, не являющиеся UUID, пропускаются.

```
GET /api/v1/content/archives/{language}/{request_id}.zip
```

Отдаёт сохранённый zip (`application/zip`, имя
`delivery-<request_id>.zip`) без повторной сборки и без изменения стока.
Если архива нет или истёк TTL — `404`.

## Идемпотентность

Повтор с тем же `request_id` в пределах TTL возвращает тот же файл архива и
не трогает новые файлы в стоке. Если zip старше TTL, он удаляется, и выдача
собирается заново с тем же `request_id`.

## Очистка

Очистку выполняет APScheduler в процессе API:

- `AsyncIOScheduler`, `CronTrigger(minute=0)` — раз в час;
- job id `delivery-archive-cleanup`, `max_instances=1`, `coalesce=True`;
- один прогон очистки выполняется при старте API.

Планировщик не запускает Prefect flow и не требует воркера. Ошибки очистки
(нет прав, гонка при удалении) логируются и не роняют API.

## Пример

```bash
curl -f -X POST http://127.0.0.1:8000/api/v1/content/deliveries \
  -H "Authorization: Bearer $CONTENT_FACTORY_DELIVERY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"language":"ru","count":2,"request_id":"3f1c1b0e-6f1a-4f6e-9d2e-0f6a7b8c9d10"}' \
  -o delivery.zip
```

Проверка содержимого:

```bash
unzip -l delivery.zip
```

## Сеть

В compose API кроме сети `default` подключён к внутренней сети `content-bus`
с alias `content-factory-api`. Публичный порт остаётся только на loopback
(`127.0.0.1:8000:8000`). Каталог `archive/` создаёт `init` от root и отдаёт
пользователю `10001`; он находится вне SFTP-chroot `content/`.

## Граничные случаи

- Пустой или отсутствующий `content/<language>/` — `204`.
- Меньше файлов, чем `count` — отдаются все.
- Нет строки в `videos`/`downloads` или пустое описание — fallback title,
  выдача работает.
- БД недоступна — fallback titles; при `query` совпадений нет, ответ `204`.
- `query` задан, но совпадений нет — `204`, сток не меняется.
- Архив удалён между списком и скачиванием — `404`, не `500`.
- Ошибка сборки zip — `.tmp` удаляется, исходники остаются, ответ `500`.
- Ошибка удаления исходника после сборки — логируется, zip отдаётся.
- `language="ru/"`, `count=0`, `count=11`, не-UUID — `422`.
