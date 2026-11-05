# Задачи «Измеритель петли» (для GitHub Issues)

Готовые титулы + тела. Источник дизайна — `retro/drafts/PDR-034-metrics-collector.md`.

---

## 1. Интерфейс метрики (формат результата тула)

**Тело:** Метрик-тул печатает одну строку JSON в stdout и завершается кодом:
`{"role":"AR","metric":"adr_frozen","status":"ok|violation|unknown","value":"3/3","detail":""}`.
Exit: 0=ok, 1=violation, 2=unknown. Один общий хелпер-контракт (документация + пример), чтобы все тулы отдавали одинаково.

**Готово когда:** описан контракт + 1 пример тула, который ему соответствует.

## 2. Коллектор `tools/metrics.py` + `SCOREBOARD.md`

**Тело:** Читает `projects.json` → `workbench`; для каждой роли прогоняет её тул на `workbench`; парсит JSON; по триггерам из `Roles/<Роль>/Whip.md`/`Carrot.md` пишет `SCOREBOARD.md` (таблица: роль → метрика → статус → значение).

**Готово когда:** `python tools/metrics.py --check` локально прогоняет тулы и печатает SCOREBOARD; статус по каждой роли без агента.

## 3. Cron `.github/workflows/metrics.yml` (LoopMaker)

**Тело:** `schedule` + `workflow_dispatch`; checkout LoopMaker + checkout проекта (из `projects.json`, публичный → без PAT) → `python tools/metrics.py` → коммит `SCOREBOARD.md`.

**Готово когда:** workflow запускается, `SCOREBOARD.md` обновляется в LoopMaker.

## 4. `check_adr_frozen` (@AR)

**Тело:** Для каждого ADR со статусом `Принято` — hash тела (файл минус `Статус:` и секция `Последствия`), сравнение с манифестом `ADL/.frozen.json`; расхождение = violation. Манифест фиксируется в момент принятия.

**Готово когда:** изменение содержания принятого ADR даёт `violation`; смена статуса/«Заведённые задачи» — нет.

## 5. `check_dtf_usage` (@BE)

**Тело:** Позитивно — ссылки на библиотеки DigiTFactory в `6-msa` (должны быть); негативно — своя инфраструктура (`CREATE TABLE`, свой EventStore/repo, ручной `SaveChangesAsync`) = violation.

**Готово когда:** микросервис на DTF без своего = ok; своя реализация = violation.

## 6. `check_fe_version` (@FE)

**Тело:** `git diff -- 8-front` → проверка, что `APP_VERSION` и `CACHE` бампнуты парой; изменён, но не бампнут = violation.

**Готово когда:** изменение `8-front` без бампа версии = violation; с бампом = ok.

## 7. Доставка (локальный плагин)

**Тело:** Плагин читает `SCOREBOARD.md` → пишет текст кнута/пряника в outbox роли (кнут = факт + ссылка на правило, «отвечать не нужно»).

**Готово когда:** при `violation` роль получает кнут-строку в outbox; при `ok` — пряник.
