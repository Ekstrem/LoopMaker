# Измеритель петли (проект-агностик)

> Петля (роли, тулы, кнут/пряник) живёт в LoopMaker и переживает смену проекта.
> Проект — только данные + конфиг. Текущий проект: `projects.json`.

## Конфиг переключения проекта

`projects.json` хранит ссылку на проект: `repo` (GitHub URL) + `workbench` (локальный чекаут).
Переключение с XDiary на другой проект = смена `current` + добавление записи в `projects`.

## Компоненты (порядок внедрения)

1. **Конфиг проекта** — `projects.json` (repo + workbench). ✅ (есть)
2. **Интерфейс метрики** — тул отдаёт машиночитаемый результат: `{role, metric, value, status}`.
   Статус: `ok` (пряник) / `violation` (кнут). Без агента — только числа/флаги.
3. **Метрик-тулы** (проект-специфичны, читают `workbench`):
   - `check_adr_frozen` — hash тела ADR (кроме статуса + «Заведённые задачи») → @AR;
   - `check_dtf_usage` — библиотеки DTF используются + нет своих → @BE;
   - `check_fe_version` — `APP_VERSION`+`CACHE` парой при изменении `8-front` → @FE;
   - универсальный: `chat_lint` (формат + будущее + фантом) → все.
4. **Коллектор** — `metrics.py`: читает `projects.json` → прогоняет тулы → сверяет с триггерами
   из `Roles/<Роль>/Whip.md`/`Carrot.md` → пишет `SCOREBOARD.md` (таблица «роль → метрика → статус»).
5. **Cron** — `.github/workflows/metrics.yml` в LoopMaker: `actions/checkout` проекта (public, без PAT) →
   коллектор → `SCOREBOARD.md` в LoopMaker.
6. **Доставка** — локальный плагин читает `SCOREBOARD.md` → пишет текст кнута/пряника в outbox роли
   (кнут = факт + ссылка на правило, «отвечать не нужно»).

## Разделение generic / project-specific

| Слой | Где |
|---|---|
| Коллектор, cron, интерфейс метрики, механизм кнут/пряник | **LoopMaker** (generic) |
| Метрик-тулы, триггеры, зоны, тексты | **проект** (через `projects.json` + `Roles/<Роль>/Whip|Carrot.md`) |

## Задачи (GitHub Issues)

- [ ] `projects.json` + переключение проекта
- [ ] интерфейс метрики (формат результата тула)
- [ ] `metrics.py` (коллектор) + `SCOREBOARD.md`
- [ ] `.github/workflows/metrics.yml` (cron в LoopMaker)
- [ ] `check_adr_frozen`
- [ ] `check_dtf_usage`
- [ ] `check_fe_version`
