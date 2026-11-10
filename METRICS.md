# Измеритель петли (проект-агностик)

> Петля (роли, тулы, кнут/пряник) живёт в LoopMaker и переживает смену проекта.
> Проект — только данные + конфиг. Текущий проект: `projects.json`.

---

# РЕЕСТР: что мы меряем (читать здесь)

Правило: **новую метрику заводим только с вычеркиванием другой** — иначе реестр растёт
быстрее, чем читается. Статусы: **живёт** · **пилот** · **драфт** · **парковка**.

## A. Метрики ролей (дашборд `SCOREBOARD.md`)

| # | Метрика | Вопрос | Чем | Порог | Статус |
|---|---|---|---|---|---|
| 1 | `adr_frozen` (@AR) | заморожены ли принятые ADR | `tools/metrics/check_adr_frozen.py` | 0 изменений «Принято» | **живёт** |
| 2 | `dtf_usage` (@BE) | пишут ли через Data Transfer Format | `tools/metrics/check_dtf_usage.py` | нет `CREATE TABLE`/`SaveChanges` в обход | **живёт** |
| 3 | `fe_version` (@FE) | растёт ли версия фронта с изменениями | `tools/metrics/check_fe_version.py` | версия > прошлой | **живёт** |

## B. Гейты в CI — 40 инструментов

| Группа | Что проверяет | Инструменты |
|---|---|---|
| **Чат и формат** (5) | потерянные сообщения, маркеры конфликта, смена статуса строкой, висящие решения | `chat_lint`, `check_nul`, `check_status_chat`, `check_awaits`, `check_keepalive` |
| **Доки и процесс** (6) | бюджет инструкций, утечки путей/токенов в доках, PDR, канон, синк копий, словарь статусов | `check_docs_size`, `check_rule_docs`, `check_pdr`, `check_canon`, `sync_tools`, `status_lint` |
| **Архитектура и канон** (10) | ADR (форма/статус/ссылки), индекс API ↔ код, EDM-маркер, сервисная авторизация, read-модели ↔ DDL/маппинг, пагинация, EventStore | `check_adr`, `check_adr_status`, `check_adr_refs`, `check_api_index`, `check_edm`, `check_service_auth`, `check_readstore_ddl`, `check_readstore_mapping`, `check_null_paging`, `check_eventstore_registration` |
| **Инфра и деплой** (7) | lock ↔ код ↔ реестр, дрейф хостов в `.env`, порты, пути/форма workflow, сигнал простоя | `gen_images_lock`, `check_lock`, `check_env_hosts`, `check_ports`, `check_workflow_paths`, `check_workflow_shape`, `whip` |
| **Тесты и фронт** (3) | изоляция тестов, тест-карта, e2e фронта | `check_test_isolation`, `check_test_map`, `check_front_e2e` |
| **Учёт** (4) | таблица статусов, Issues/Projects, «стоит без движения», здоровье петли | `stats`, `sync_board`, `sweep`, `health` |
| **Секреты** (2) | утечки и фрагменты ключей | `check_secrets`, `test_check_secrets` |
| **Git-механика** (3) | синхронизация и её состояние, ссылки в статистике | `test_git_sync`, `test_git_sync_state`, `test_stats_ref` |

## C. Драфты, пилот и парковка (НЕ в дашборде)

| # | Метрика | Вопрос | Замер 2026-11-10 | Статус |
|---|---|---|---|---|
| 4 | `review_age` | сколько карточка висит «На проверке» у ревьюера | у @AR 46 карточек, у @TL 200 | **предлагаю живую** (PDR-042) |
| 5 | `chatter` | «написал / адресовано ему» — шум роли | @AR 2.5, @TL 0.6, @BE 0.3 | **предлагаю живую** (PDR-041/042) |
| 6 | `delivered_reacted` | доставлено строк против потребовавших хода | 520 сообщений / 204 чат-онли коммита | **пилот** (PDR-041) |
| 7 | `missed_critical` | крит, о котором роль узнала позже часа | 0 в пилоте @LM | **пилот** (PDR-041) |
| 8 | `canon_churn` | правок канона в день без нового ADR | `edm-canon` 14 + `api-index` 8 при 0 ADR | **парковка** (покрывается 4+5) |
| 9 | `closure_gap` | закрытые карточки без вердикта ревьюера | не мерилось | **парковка** |
| 10 | `review_dup` | двойное ревью одной карточки | не мерилось | **парковка** |

## D. Итог по сокращению

**Живут 5:** `adr_frozen`, `dtf_usage`, `fe_version`, `review_age`, `chatter`.
**Пилот 2:** `delivered_reacted`, `missed_critical` (только пока идёт пилот).
**Парковка 3:** `canon_churn`, `closure_gap`, `review_dup`.

## E. Где смотреть

`SCOREBOARD.md` (дашборд ролей) · прогон `ci-guards` в XDiary (40 гейтов, таблица B) · этот файл.

---

# Движок метрик (design, проект-агностик)

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
