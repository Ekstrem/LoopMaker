# PDR-034. Измеритель петли (метрики кнута/пряника, проект-агностик)

| Поле | Значение |
|---|---|
| **Статус** | черновик |
| **Автор** | @LM |
| **Затронет** | LoopMaker (`projects.json`, `tools/`, `.github/workflows/`), роли (`Roles/<Роль>/Whip.md` + `Carrot.md`) |
| **Дата** | 2026-11-05 |

## Проблема

Кнут/пряник написан (`Roles/<Роль>/Whip.md` + `Carrot.md`), но метрики **не измеримы тулом**:
сейчас их оценивает агент → кнут вырождается в спор («я считаю, что ты нарушил»), а не в факт.
Второе: метрики привязаны к XDiary — при смене проекта их приходится переписывать, а петля должна остаться.

## Предложение

Разделить **механизм** (generic, LoopMaker) и **метрики** (проектные, через `projects.json`).

### 1. Конфиг проекта — `projects.json` (✅ есть)

```json
{ "current": "xdiary", "projects": { "xdiary": { "name": "XDiary", "repo": "https://github.com/Ekstrem/XDiary", "workbench": "../XDiary" } } }
```

Переключение проекта = смена `current` + запись в `projects`.

### 2. Интерфейс метрики (что отдаёт тул)

Каждый метрик-тул печатает **одну строку JSON** в stdout и завершается кодом:

```json
{"role": "AR", "metric": "adr_frozen", "status": "ok", "value": "3/3", "detail": ""}
```

- `status`: `ok` (пряник) / `violation` (кнут) / `unknown` (не измерено);
- exit-код: `0` = ok, `1` = violation, `2` = unknown. **Без агента — только числа/флаги.**
- **`unknown` → ретрай N раз** (транзиент), только потом считать статус; `violation` не ретраится.
- **Контекст замера — обязательное поле** (`ref`/`measured-on`): без него результат = `unknown`.

### 3. Коллектор — `tools/metrics.py`

1. читает `projects.json` → `workbench` (локальный путь к проекту);
2. для каждой роли находит её метрик-тул и прогоняет его на `workbench`;
3. парсит JSON-строку → определяет `ok`/`violation`;
4. пишет `SCOREBOARD.md` (таблица: роль → метрика → статус → значение).

Триггеры (пороги) — из `Roles/<Роль>/Whip.md`/`Carrot.md` (уже записаны).

### 4. Cron — `.github/workflows/metrics.yml` (LoopMaker)

```yaml
on:
  schedule: [{cron: '*/15 * * * *'}]
  workflow_dispatch:
jobs:
  metrics:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4            # LoopMaker
      - uses: actions/checkout@v4            # проект (из projects.json)
        with: { repository: Ekstrem/XDiary, path: workbench }
      - run: python tools/metrics.py
      - run: git commit -am "metrics: scoreboard" && git push || true
```

Проект публичный → checkout **без PAT**. Пишет `SCOREBOARD.md` в LoopMaker.

### 5. Доставка — локальный плагин (не CI)

Плагин читает `SCOREBOARD.md` → пишет текст кнута/пряника в outbox роли
(кнут = факт + ссылка на правило, «отвечать не нужно»).

### 6. Проектные метрик-тулы (в `tools/metrics/`)

- **`check_adr_frozen`** (@AR): для каждого ADR со статусом `Принято` — hash тела
  (файл минус `Статус:` и секция `Последствия`), сравнение с манифестом `ADL/.frozen.json`;
  расхождение = `violation`.
- **`check_dtf_usage`** (@BE): позитивно — ссылки на библиотеки DigiTFactory в `6-msa`
  (должны быть); негативно — своя инфраструктура (`CREATE TABLE`, свой EventStore/repo)
  = `violation`.
- **`check_fe_version`** (@FE): `git diff -- 8-front` → проверка, что `APP_VERSION` и `CACHE`
  бампнуты парой; изменён, но не бампнут = `violation`.

## Цена

- 3 тула × ~полдня; `metrics.py` + `metrics.yml` × ~полдня.
- Риск: тулы читают проект (checkout публичный → без PAT).

## Как проверить

- `python tools/metrics.py --check` — локально прогоняет тулы и печатает SCOREBOARD;
- `metrics.yml` — cron запускается, `SCOREBOARD.md` обновляется;
- метрика: в `SCOREBOARD.md` статус по каждой роли вычислен **без агента**.

## Что мешает принять сейчас

- Реализация тулов (после согласия владельца; драфт готов).
- `gh` токен для выдвижения задач в Issues (в процессе).

## Что сказали роли

- (пусто)
