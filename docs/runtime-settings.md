# Runtime-настройки

HTTP-контракт для чтения, проверки и правки `app_data/config/config.yaml`
без SSH. Файл остаётся источником правды: следующий прогон воркера читает его
заново, поэтому перезапуск не нужен. Правки ограничены безопасным срезом;
`captcha`, `alerts`, паузы этапов, `run.pause_between_languages` и
переменные `SUPPLIER_*` через API не меняются.

## Авторизация

Все три маршрута требуют тот же Bearer-токен, что и остальные
`/api/v1/content/*`:

```
Authorization: Bearer <CONTENT_FACTORY_DELIVERY_TOKEN>
```

- токен не задан в окружении — `503`, выдача выключена;
- токен неверный или заголовка нет — `401`;
- токен и содержимое конфига не логируются.

## Чтение настроек

```
GET /api/v1/content/settings
```

Ответ:

```json
{
  "languages": [
    {"code": "ru", "enabled": true, "stock_target": 150},
    {"code": "en", "enabled": false, "stock_target": 100}
  ],
  "run": {"batch": 20, "interval": "6h", "max_duration": "45m"},
  "search": {"max_queries": 30},
  "filter": {"max": 60},
  "download": {"workers": 2},
  "delivery": {"max_count": 100}
}
```

| Поле | Значение |
|---|---|
| `languages[].code` | Код языка из `languages` в файле |
| `languages[].enabled` | Производить для этого языка |
| `languages[].stock_target` | Целевой остаток готовых видео |
| `run.batch` | Максимальный объём производства за один тик |
| `run.interval` | Как часто запускать производство |
| `run.max_duration` | Мягкий лимит одного запуска |
| `search.max_queries` | Поисковых запросов за запуск суммарно |
| `filter.max` | Найденных видео на фильтры за запуск суммарно |
| `download.workers` | Параллельных скачиваний |
| `delivery.max_count` | Максимум видео в одном архиве ручной выдачи |

Длительности отдаются строками в том виде, как они записаны в файле
(`6h`, `45m`). Если файла нет, `load_runtime_config(seed=True)` создаёт
шаблон с комментариями, а `GET` возвращает значения по умолчанию. Если файл
есть, но пуст, возвращаются значения по умолчанию без перезаписи файла.

## Preview

```
POST /api/v1/content/settings/preview
Content-Type: application/json
```

Тело — тот же объект, что возвращает `GET`. Ответ:

```json
{
  "changed": true,
  "diff": "--- config.yaml\n+++ config.yaml\n@@ -1,7 +1,7 @@\n...",
  "settings": {
    "languages": [{"code": "ru", "enabled": true, "stock_target": 150}],
    "run": {"batch": 20, "interval": "6h", "max_duration": "45m"},
    "search": {"max_queries": 30},
    "filter": {"max": 60},
    "download": {"workers": 2},
    "delivery": {"max_count": 100}
  }
}
```

`diff` — unified diff между текущим и новым текстом файла. Preview ничего не
пишет: ни файл, ни бэкап не создаются. Если изменения пустые, ответ содержит
`changed: false` и `diff: ""`.

Валидация выполняется тем же кодом, что и при загрузке конфига:
неизвестные ключи, типы, диапазоны и проверка
`run.pause_between_languages <= run.max_duration`. Неизвестное поле в теле
запроса даёт `422`.

## Применение

```
PUT /api/v1/content/settings
Content-Type: application/json
```

Тело — тот же объект, что у `GET`. Ответ:

```json
{
  "changed": true,
  "diff": "--- config.yaml\n+++ config.yaml\n@@ ...",
  "backup": "config.yaml.bak-20260501-120000",
  "settings": {
    "languages": [{"code": "ru", "enabled": true, "stock_target": 150}],
    "run": {"batch": 20, "interval": "6h", "max_duration": "45m"},
    "search": {"max_queries": 30},
    "filter": {"max": 60},
    "download": {"workers": 2},
    "delivery": {"max_count": 100}
  }
}
```

Порядок записи:

1. Настройки повторно валидируются после наложения на текущий файл.
2. Создаётся бэкап `config.yaml.bak-<YYYYmmdd-HHMMSS>` с текущим содержимым.
3. Новый текст пишется во временный файл в `config/` и заменяет
   `config.yaml` через `os.replace`.
4. При ошибке записи старый файл остаётся целым, временный файл удаляется.

`ruamel.yaml` работает в round-trip режиме: комментарии, порядок ключей и
кавычки сохраняются. Меняются только поля из безопасного среза; `captcha`,
`alerts`, паузы этапов и прочие ключи остаются как были. Языки не
добавляются и не удаляются: меняются только `enabled` и `stock_target`.
Если в файле нет `delivery.max_count`, preview/apply добавляет ключ со
значением из тела.

При отсутствии изменений ответ содержит `changed: false`, `backup: null` и
`diff: ""`; файл не трогается и бэкап не создаётся. Preview и apply
сериализуются in-process lock, поэтому одновременные запросы не теряют
версии: каждая реальная запись оставляет свой бэкап.

## Диапазоны

| Поле | Диапазон |
|---|---|
| `run.batch` | 1..1000 |
| `search.max_queries` | 1..500 |
| `filter.max` | 1..10000 |
| `download.workers` | 1..8 |
| `delivery.max_count` | 1..300 |
| `languages[].stock_target` | 0..100000 |
| `run.interval`, `run.max_duration` | непустая строка вида `6h`, `45m`, больше нуля |

Дополнительно `run.pause_between_languages` (не редактируется через API) не
может превышать `run.max_duration`. Значения проверяются как строгие типы:
`true` вместо числа, `"20"` вместо `20` и `1` вместо `true` отклоняются.

## Ошибки

| Код | Когда |
|---|---|
| `401` | Нет заголовка или токен неверный |
| `422` | Невалидное тело, неизвестное поле, неизвестный язык, добавление/удаление языка, неверная длительность, диапазон или битый YAML |
| `500` | Конфиг не читается на `GET` или не записывается на `PUT` |
| `503` | `CONTENT_FACTORY_DELIVERY_TOKEN` не задан |

При `422` и `500` записи файл не меняется. Текст ошибки не содержит токенов
и переменных окружения.

## Примеры

```bash
curl -f http://127.0.0.1:8000/api/v1/content/settings \
  -H "Authorization: Bearer $CONTENT_FACTORY_DELIVERY_TOKEN"

curl -f -X POST http://127.0.0.1:8000/api/v1/content/settings/preview \
  -H "Authorization: Bearer $CONTENT_FACTORY_DELIVERY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"languages":[{"code":"ru","enabled":true,"stock_target":150},{"code":"en","enabled":false,"stock_target":100}],"run":{"batch":30,"interval":"6h","max_duration":"45m"},"search":{"max_queries":30},"filter":{"max":60},"download":{"workers":2},"delivery":{"max_count":100}}'

curl -f -X PUT http://127.0.0.1:8000/api/v1/content/settings \
  -H "Authorization: Bearer $CONTENT_FACTORY_DELIVERY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"languages":[{"code":"ru","enabled":true,"stock_target":150},{"code":"en","enabled":false,"stock_target":100}],"run":{"batch":30,"interval":"6h","max_duration":"45m"},"search":{"max_queries":30},"filter":{"max":60},"download":{"workers":2},"delivery":{"max_count":100}}'
```
