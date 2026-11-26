# Слоистый конвейер производства

Производство описано не списком независимых шагов, а цепочкой слоёв:
генерация → проверка → следующая генерация → проверка. Ядро конвейера
(`services/production/pipeline.py`) не знает конкретных producers и проверок;
их передают реестры. На этом конвейере работают `flows/generate_batch`,
Vast/ComfyUI и фото-карточки.

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
- `LayerOutcome` — `layer`, принятые `artifacts`, `failures` слоя и
  `produced` — все артефакты producer'а, включая отклонённые проверкой.
- `PipelineResult` — `layers`, `artifacts` последнего слоя, `failures` всех
  слоёв.
- `RecipeProfile` — поля профиля, которые читают группировка и flow:
  `name`, `language`, `production`.
- `PipelineContext` — общий вход прогона: `profiles`, `count`, `data_dir`,
  `settings`, `run_id`, `run_dir`, `dry_run`, `rng`, `now`. `run_id` —
  обязательный идентификатор группы профилей, выбранный flow до запуска
  producer'ов; он проверяется по шаблону `YYYYMMDDTHHMMSSZ[-N]`. Vast-слой
  читает `run.json` только ради статусов и ошибок: имена файлов берутся из
  плана, а элементы чужого профиля или с путём вместо имени отклоняются.

Порты — там же, в `schemas/generator/pipeline.py`:

- `LayerProducer.supported_outputs` — какие `ArtifactKind` умеет producer;
  `plan_layer(layer, context)` строит план без генерации;
  `produce_layer(layer, inputs, context)` делает артефакты из объявленных
  входов.
- `LayerCheck.check(layer, artifact, context)` возвращает `CheckResult`.

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
планы слоёв, `failures` пуст. Для Vast план — looks/seeds/frames, для
фото — имена файлов и статус `dry_run`.

## Реестры

`ProducerRegistry` — в `src/content_factory/services/production/registry.py`,
`CheckRegistry` — в `src/content_factory/services/production/qa.py`. Оба
принимают фабрики `Callable[[], LayerProducer]` и
`Callable[[], LayerCheck]`, поддерживают `register`, `resolve`, `available`,
`reset`. Пустое имя запрещено, повторная регистрация заменяет запись,
`reset` очищает реестр.

Встроенные producers:

- `vast` — `services/production/vast.py`, видео через существующий
  `services.generator.generate_batch`; на группу — один вызов со всеми
  профилями;
- `card` — `services/production/card.py`, фото-пост из текста и карточки.

Встроенные checks — `ArtifactCheck` в `services/production/checks.py` с
verifier'ами `mp4_reason`, `source_reason`, `image_reason`; имена проверок
в рецепте — `mp4`, `clef`, `image`. Обязательные проверки внутри producers
не снимаются.

## Рецепт профиля

Каждый шаг `production` из `profile.yaml` становится корневым слоем:

```python
Layer(
    name="vast",
    producer="vast",
    inputs=(),
    output=ArtifactKind.VIDEO,
    checks=("clef", "mp4"),
)
```

Несколько шагов — несколько независимых цепочек одного прогона. Профили
группируются по точной сигнатуре слоёв (имя, producer, входы, выход,
проверки); группа получает один `run_id` и один `run_pipeline`. Отчёт
`run.json` каждого профиля flow строит из `LayerOutcome.produced` и
`failures`, поэтому в нём остаются и принятые, и отклонённые items.

## Проверки

```bash
python -m pytest tests/unit/production -q --no-cov
ruff check .
ruff format --check .
mypy src main.py
```

Тесты ядра — `tests/unit/production/test_pipeline.py`; группировки —
`tests/unit/production/test_groups.py`. Они используют фейковые producer'ы и
проверки, без Vast, LLM и сети.

## Вне scope

- Новые виды контента и новые producers.
- Уникализатор, очередь и General/Profile.
- Сток и выдача не затрагиваются.
