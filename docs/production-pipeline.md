# Слоистый конвейер производства

Issue #119 добавляет ядро конвейера, в котором производство описано не
списком независимых шагов, а цепочкой слоёв: генерация → проверка → следующая
генерация → проверка. Ядро живёт отдельно от действующего пути
`flows/generate_batch` и не меняет поведение прода: старые `Producer`,
`ProducedBatch` и `run_batch` остаются до issue #120, которое переведёт
Vast/ComfyUI и карточки на новый конвейер.

## Контракты

Схемы — `src/content_factory/schemas/generator/pipeline.py`:

- `ArtifactKind` — вид контента слоя: `video`, `text`, `photo`.
- `Artifact` — один файл слоя: `item`, `profile`, `kind`, `path`,
  `media_type`, `meta`. Пара `(profile, item)` — идентичность item'а; слой,
  который преобразует вход, сохраняет её.
- `Layer` — шаг рецепта: `name`, `producer`, `inputs`, `output`, `checks`.
  `inputs` — имена только предыдущих слоёв, `checks` по умолчанию пуст.
- `CheckResult` — вердикт проверки: `passed`, `reason` (может быть `None`).
- `ItemFailure` — item, не дошедший до конца: `item`, `profile`, `layer`,
  `reason`.
- `LayerOutcome` — `layer`, принятые `artifacts`, `failures` слоя.
- `PipelineResult` — `layers`, `artifacts` последнего слоя, `failures` всех
  слоёв.

Порты — `src/content_factory/services/production/protocol.py`:

- `LayerProducer.supported_outputs` — какие `ArtifactKind` умеет producer;
  `plan_layer(layer, context)` строит план без генерации;
  `produce_layer(layer, inputs, context)` делает артефакты из объявленных
  входов.
- `LayerCheck.check(layer, artifact, context)` возвращает `CheckResult`.
- `PipelineContext` — общий вход прогона: `profiles`, `count`, `data_dir`,
  `settings`, `run_dir`, `dry_run`, `rng`, `now`.

## Движок

`run_pipeline(context, layers, *, producers=None, checks=None)` находится в
`src/content_factory/services/production/pipeline.py`. Движок не импортирует
конкретные producers и checks и не содержит ветвлений по producer'у,
проверке, теме или типу контента: зависимости приходят только через
реестры.

Порядок работы:

1. Рецепт целиком проверяется до первой генерации.
2. Слои выполняются в порядке списка. Вход слоя — артефакты перечисленных
   предыдущих слоёв, в порядке объявления входов.
3. Producer получает только эти входы. Его артефакты проверяются на контракт.
4. Проверки слоя выполняются на каждом артефакте в объявленном порядке.
   Первая упавшая проверка останавливает item и исключает его из следующих
   слоёв; остальные items продолжают путь.
5. `PipelineResult.failures` собирает провалы всех слоёв; `artifacts` —
   то, что осталось после последнего слоя.

## Валидация рецепта

До запуска `ProfileError` получают:

- пустой список слоёв, пустой список профилей, `count <= 0`;
- пустое или повторное имя слоя;
- `inputs` на несуществующий или будущий слой;
- неизвестный producer или check;
- `output`, которого нет в `supported_outputs` producer'а.

Нарушение контракта при выполнении — `GeneratorError`:

- producer вернул артефакт другого `kind`;
- артефакт чужого `profile` или `item`;
- повтор пары `(profile, item)`;
- producer первого слоя вернул не по `count` артефактов на профиль;
- producer или проверка упали не с `GeneratorError` (обёрнуто с причиной).

Отсутствие артефакта для известного входного item — не исключение, а
`ItemFailure` слоя: остальные items продолжают конвейер. Если проверка
вернула `passed=False` без причины, в отчёте будет причина по умолчанию с
именем проверки.

## Dry run

При `context.dry_run=True` для каждого слоя вызывается только
`plan_layer(layer, context)`. `produce_layer` и проверки не вызываются,
файлы и БД не пишутся, сеть не трогается. `PipelineResult.layers` содержит
планы слоёв, `failures` пуст.

## Реестры

`ProducerRegistry` — в `src/content_factory/services/production/registry.py`,
`CheckRegistry` — в `src/content_factory/services/production/qa.py`. Оба
принимают фабрики `Callable[[], LayerProducer]` и
`Callable[[], LayerCheck]`, поддерживают `register`, `resolve`, `available`,
`reset`. Пустое имя запрещено, повторная регистрация заменяет запись,
`reset` очищает реестр. Конкретные производители и проверки в этом issue не
регистрируются: это делает следующий этап.

## Пример рецепта

```python
from content_factory.schemas.generator.pipeline import ArtifactKind, Layer

layers = (
    Layer(name="source", producer="vast", inputs=(), output=ArtifactKind.VIDEO),
    Layer(
        name="caption",
        producer="text",
        inputs=("source",),
        output=ArtifactKind.TEXT,
        checks=("text_length",),
    ),
    Layer(
        name="render",
        producer="overlay",
        inputs=("source", "caption"),
        output=ArtifactKind.VIDEO,
        checks=("media",),
    ),
)
```

Здесь producer'ы и проверки — только имена: движок разрешает их через
переданные реестры.

## Проверки

```bash
python -m pytest tests/unit/production -q --no-cov
ruff check .
ruff format --check .
mypy src main.py
```

Тесты ядра — `tests/unit/production/test_pipeline.py`; они используют
фейковые producer'ы и проверки, без Vast, LLM и сети.

## Вне scope

- Перевод `vast`, `card` и `flows/generate_batch` на конвейер — issue #120.
- Удаление старых `Producer` и `ProducedBatch` — issue #120.
- Конкретные проверки, новые producers, уникализатор и очередь.
- General/Profile, сток и выдача не затрагиваются.
