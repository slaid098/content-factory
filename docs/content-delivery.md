# Выдача контента

HTTP-контракт для воркеров, которым нужны готовые видео по языку без доступа
к SFTP-каталогам. Сервис сам выбирает файлы из стока, упаковывает их в zip и
удаляет выданные `.mp4` из `content/<language>/`.

Есть два режима:

- синхронный `POST /api/v1/content/deliveries` — zip отдаётся потоком, контракт
  YouTube Kit не меняется;
- асинхронный `POST /api/v1/content/deliveries/link` — архив собирается в фоне,
  заливается на Litterbox на 24 часа, а на сервере остаётся sidecar со ссылкой.

## Синхронная выдача

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
| `count` | целое от 1 до 300 |
| `request_id` | UUID |
| `query` | необязательная тема, строка от 1 до 200 символов |

Фактический максимум `count` задаёт runtime-конфиг:

```yaml
delivery:
  # Максимум видео в одном архиве ручной выдачи.
  max_count: 100
```

Допустимы значения 1–300. Если `count` больше `delivery.max_count` или 300,
API отвечает `422`. Конфиги без секции `delivery` получают `max_count: 100`
через merge с дефолтами.

Если `query` не передан, выбираются файлы всех тем. Если передан — только
файлы, у которых `videos.query` совпадает точно, с учётом регистра.

## Авторизация

Заголовок `Authorization: Bearer <CONTENT_FACTORY_DELIVERY_TOKEN>`.

- Токен не задан в окружении — `503`, выдача выключена.
- Токен неверный или заголовка нет — `401`.
- Токен не логируется и не попадает в ответы.

## Ответы синхронной выдачи

| Код | Когда | Тело |
|---|---|---|
| `200` | есть хотя бы один `.mp4` | `application/zip`, `Content-Disposition: attachment; filename="delivery-<request_id>.zip"` |
| `204` | в `content/<language>/` нет `.mp4` | пусто |
| `401` | нет заголовка или токен неверный | JSON с ошибкой |
| `422` | тело не прошло валидацию или `count` выше лимита | JSON с ошибкой |
| `500` | не удалось собрать или записать zip | JSON с ошибкой |
| `503` | `CONTENT_FACTORY_DELIVERY_TOKEN` не задан | JSON с ошибкой |

Если файлов меньше, чем `count`, отдаются все имеющиеся.

## Ручная выдача ссылкой

```
POST /api/v1/content/deliveries/link
```

Тело — как у синхронного запроса. Ответ `202`:

```json
{
  "request_id": "11111111-1111-4111-8111-111111111111",
  "language": "ru",
  "status": "building",
  "created_at": "2026-05-01T12:00:00Z",
  "expires_at": "2026-05-02T12:00:00Z",
  "size": 0,
  "files": 0,
  "url": null,
  "error": null
}
```

Архив собирается существующим `deliver()` в фоне. Исходные `.mp4` удаляются
только после успешной записи zip, как и в синхронном режиме.

```
GET /api/v1/content/deliveries/{language}/{request_id}
```

Возвращает те же поля и текущий статус:

| Статус | Значение |
|---|---|
| `building` | сборка или заливка ещё идёт |
| `ready` | архив залит, `url` — публичная ссылка Litterbox |
| `ready_local` | заливка не удалась, zip остался локально, `url=null`, `error` объясняет причину |
| `empty` | в стоке не было `.mp4`, zip не создавался, `files=0` |
| `failed` | сборка не удалась, `error` содержит причину |

Ошибки:

- `401/503` — как у остальных маршрутов выдачи;
- `404` — job неизвестен или его TTL истёк (sidecar удаляется);
- `409` — другая сборка уже идёт;
- `422` — тело невалидно или `count` больше `delivery.max_count`;
- `500` — не удалось запустить сборку или сохранить её состояние.

Одновременно в процессе API выполняется только одна сборка. Повторный `POST`
с тем же `request_id` возвращает существующий статус и не списывает сток.
Пока идёт сборка, запрос с другим `request_id` получает `409`.

## Litterbox

После успешной сборки zip заливается на:

```
https://litterbox.catbox.moe/resources/internals/api.php
```

Параметры: `reqtype=fileupload`, `time=24h`, файл в поле `fileToUpload`,
HTTP-клиент — `httpx`. Ответ сервиса должен быть абсолютным HTTP(S)-URL;
иначе это ошибка заливки.

- Архив больше 1 ГБ не заливается — job получает `ready_local`.
- Litterbox недоступен, ответил ошибкой, таймаутом или не URL — job получает
  `ready_local`, zip остаётся на диске, `error` содержит понятный текст.
- После успешной заливки локальный zip удаляется.
- Полный URL Litterbox не пишется в лог и не попадает в ответы, кроме поля
  `url` самого job и списка архивов.

Ссылка Litterbox публичная и обходит IP-allowlist. Это осознанно: видео уже
публичны, а ссылка живёт 24 часа.

## Sidecar

Состояние ручной выдачи хранится рядом с архивом:

```
app_data/archive/<language>/<request_id>.json
```

Пример:

```json
{
  "request_id": "11111111-1111-4111-8111-111111111111",
  "language": "ru",
  "status": "ready",
  "created_at": "2026-05-01T12:00:00Z",
  "expires_at": "2026-05-02T12:00:00Z",
  "size": 303000000,
  "files": 100,
  "url": "https://litter.catbox.moe/example.zip",
  "error": null
}
```

Путь строится только из языка `^[a-z]{2}$` и UUID. При старте API:

- просроченные sidecar и их локальные zip удаляются;
- sidecar со статусом `building`, оставшийся от прошлого процесса, помечается
  `failed`; повторный `POST` с тем же `request_id` собирает выдачу заново.

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

Локальный архив хранится в:

```
app_data/archive/<language>/<request_id>.zip
```

Исходные `.mp4` удаляются только после успешной записи zip. Если удаление
файла не удалось, ошибка логируется, а zip всё равно отдаётся.

TTL задаётся `CONTENT_FACTORY_DELIVERY_RETENTION_HOURS` (по умолчанию `24`,
минимум `1`). В асинхронном режиме тот же TTL записывается в `expires_at`
sidecar. Просроченный job или архив отвечает `404`, sidecar удаляется.

## Список и скачивание архивов

```
GET /api/v1/content/archives
```

Возвращает массив живых архивов, новые сверху. Локальные архивы без sidecar
по-прежнему видны как `storage=local`:

```json
[
  {
    "language": "ru",
    "request_id": "3f1c1b0e-6f1a-4f6e-9d2e-0f6a7b8c9d10",
    "size": 303000000,
    "created_at": "2026-05-01T12:00:00Z",
    "expires_at": "2026-05-02T12:00:00Z",
    "storage": "remote",
    "url": "https://litter.catbox.moe/example.zip",
    "files": 100
  }
]
```

- `storage=remote` — архив на Litterbox, `url` заполнен, `files` берётся из
  sidecar;
- `storage=local` — zip на диске, `url=null`;
- для старого локального архива без sidecar `files=null`, `size` — размер файла;
- `created_at`/`expires_at` берутся из sidecar, для старого архива — из mtime
  файла.

```
GET /api/v1/content/archives/{language}/{request_id}.zip
```

- для `storage=remote` — `302 Location: <url>` на Litterbox;
- для локального архива — `FileResponse` (`application/zip`, имя
  `delivery-<request_id>.zip`) без повторной сборки и без изменения стока;
- если архива нет, он истёк или `ready_local` остался без zip — `404`.

## Идемпотентность

Повтор с тем же `request_id` в пределах TTL возвращает тот же файл архива или
тот же статус job и не трогает новые файлы в стоке. Если zip или sidecar старше
TTL, они удаляются, и выдача собирается заново с тем же `request_id`.

## Очистка

Очистку выполняет APScheduler в процессе API:

- `AsyncIOScheduler`, `CronTrigger(minute=0)` — раз в час;
- job id `delivery-archive-cleanup`, `max_instances=1`, `coalesce=True`;
- один прогон очистки и восстановления sidecar выполняется при старте API.

Планировщик не запускает Prefect flow и не требует воркера. Ошибки очистки
(нет прав, гонка при удалении) логируются и не роняют API.

## Примеры

Синхронная выдача:

```bash
curl -f -X POST http://127.0.0.1:8000/api/v1/content/deliveries \
  -H "Authorization: Bearer $CONTENT_FACTORY_DELIVERY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"language":"ru","count":2,"request_id":"3f1c1b0e-6f1a-4f6e-9d2e-0f6a7b8c9d10"}' \
  -o delivery.zip
```

Ручная выдача ссылкой:

```bash
curl -f -X POST http://127.0.0.1:8000/api/v1/content/deliveries/link \
  -H "Authorization: Bearer $CONTENT_FACTORY_DELIVERY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"language":"ru","count":100,"request_id":"11111111-1111-4111-8111-111111111111"}'

curl -f http://127.0.0.1:8000/api/v1/content/deliveries/ru/11111111-1111-4111-8111-111111111111 \
  -H "Authorization: Bearer $CONTENT_FACTORY_DELIVERY_TOKEN"
```

Проверка содержимого локального архива:

```bash
unzip -l delivery.zip
```

## Сеть

В compose API кроме сети `default` подключён к внутренней сети `content-bus`
с alias `content-factory-api`. Публичный порт остаётся только на loopback
(`127.0.0.1:8000:8000`). Каталог `archive/` создаёт `init` от root и отдаёт
пользователю `10001`; он находится вне SFTP-chroot `content/`.

## Граничные случаи

- Пустой или отсутствующий `content/<language>/` — синхронно `204`, вручную
  `empty`.
- Меньше файлов, чем `count` — отдаются все.
- Нет строки в `videos`/`downloads` или пустое описание — fallback title,
  выдача работает.
- БД недоступна — fallback titles; при `query` совпадений нет, синхронно `204`,
  вручную `empty`.
- `query` задан, но совпадений нет — сток не меняется.
- Архив удалён между списком и скачиванием — `404`, не `500`.
- Ошибка сборки zip — `.tmp` удаляется, исходники остаются; синхронно `500`,
  job переходит в `failed`.
- Ошибка удаления исходника после сборки — логируется, zip отдаётся.
- Litterbox недоступен или архив больше 1 ГБ — `ready_local`, zip остаётся.
- API перезапущен во время `building` — при старте job получает `failed`,
  повторный `POST` собирает заново.
- Два `POST` одновременно — один `202`, второй `409`; сток не списывается
  дважды.
- Старый локальный архив без sidecar — `storage=local`, как раньше.
- Sidecar `ready` с URL, но без zip — `storage=remote`; sidecar `ready_local`
  без zip — `404`.
- `language="ru/"`, `count=0`, `count=301`, не-UUID — `422`.
