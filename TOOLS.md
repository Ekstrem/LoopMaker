# Реестр инструментов XDiary — процессы, зависимости, роли

> Схематично, но полно. Четыре слоя: **плагин** (доставка чата) → **инструменты**
> (`tools/*.py`) → **workflow** (процессы CI/автоматика) → **роли** (кто пишет/использует/ревьюит).
> Источник правды инструментов — `LoopMaker/tools/` (копии → `XDiary/tools/`, сверка `sync_tools`).
> Составлено 2026-11-20 по прямому запросу владельца.

---

## План / как читать

- Каждый инструмент описан строкой: **что делает → процесс (workflow/триггер) → зависимости → роли**.
- «Роли»: **пишет** = кто правит код; **использует** = кого он гоняет/кому адресован; **ревьюит** = чей домен (по §2.1 AGENTS).
- `(копия)` = файл в `XDiary/tools/` есть, но источник правды — `LoopMaker/tools/` (правка в обоих местах + манифест `tools/.sources.json`).
- `(только XDiary)` = инструмент живёт только в XDiary, в LoopMaker его нет (манифест-`upstream` указывает на `tools/<имя>`).

---

## Слой 1 — Плагин доставки чата (`LoopMaker/lib/`, 9 файлов)

| Файл | Что делает | Зависимости | Роли |
|---|---|---|---|
| `index.js` | точка входа: цикл опроса, таймеры, склейка всех модулей | config, bridge, git, merge | пишет @LM; использует — все роли (транспорт) |
| `config.js` | читает `cordis.patch.yml`, нормализует роли/шаблоны/пороги | cordis.patch.yml | пишет @LM |
| `parse.js` | парсит строку чата `- [ts] → @X: текст` | — | пишет @LM; используется bridge |
| `bridge.js` | читает outbox'ы, маршрутизирует адресатам, эскалация/батчинг | parse, state, sessions | пишет @LM |
| `state.js` | персистит `seen`/`pending` (что уже доставлено), флаг `seeded` | файл состояния | пишет @LM |
| `sessions.js` | ищет тред роли по заголовку, ретаргет при архивации | — | пишет @LM |
| `git.js` | `commitChat`: union(dedup+sort) → worktree → PR (ветка+PR, не прямой push) | parse (SECRET_RE, mergeChatLines) | пишет @LM |
| `merge.js` | merge-сервис: листит зелёные PR и вливает (squash) | GitHub API | пишет @LM |
| `telegram.js` | дублирует уведомления в Telegram-чат | — | пишет @LM |

Процесс: **доставка чата** — bridge читает `chat/*.md` → доставляет адресату в сессию (или Telegram) → git.js синкает через PR → merge.js вливает зелёные PR.

---

## Слой 2 — Инструменты (`tools/*.py`), по процессам

> **Только в LoopMaker (не копируются в XDiary):** `chat_bot.py` (чат-бот LoopMaker),
> `deliver_metrics.py` + `metrics.py` + `metrics/` (сбор/доставка метрик), `chatbot.config.json`.
> Эти четыре — внутренняя автоматика самого LoopMaker, не участвуют в петле XDiary.

### 2.1 Транспорт и формат чата

| Инструмент | Что делает | Процесс/триггер | Роли |
|---|---|---|---|
| `chat_sync.py` (копия) | фоновый копировщик: worktree-чат → общий `chat/*.md` (append, dedup rstrip), раз в 5 с | Task Scheduler `XDiaryChatSync` | пишет @LM; использует все роли |
| `chat_inbox.py` (копия) | находит вопросы без ответа (rc=1, если есть) | координатор вручную | пишет @LM; использует @TL |
| `chat_lint.py` (копия) | линтер формата: адресат без @, дубли, порядок, strict-times | `ci-guards` (check-chat) | пишет @LM; использует @TL/@LM |
| `chat_rotate.py` (копия) | архивирует разросшиеся outbox'ы | вручную | пишет @LM |
| `restore_chat.py` (только XDiary) | восстановление чата (union) после простоя бота | вручную (@LM) | пишет @LM |
| `check_owner_chat.py` (копия) | следит, что владелец видит чат | `ci-guards` | пишет @LM |
| `check_status_chat.py` (только XDiary) | формат статус-строк в чате | `ci-guards` | пишет @LM |
| `check_chat_shrink.py` (только XDiary) | контролирует сжатие чата | `ci-guards` | пишет @LM |

### 2.2 Гейты/линтеры — домен (по папке, ревью по §2.1)

| Инструмент | Что делает | Домен | Роли |
|---|---|---|---|
| `check_secrets.py` (копия) | секреты/фрагменты в чате/задачах/доках | безопасность | пишет @LM; ревью @DO |
| `check_paths.py` (копия) | роль не вышла за свою зону (ядро+служебное) | зоны ролей | пишет @LM; ревью @DO |
| `check_direct_push.py` (только XDiary) | ловит прямой push в main (не через PR) | git-дисциплина | пишет @LM |
| `check_docs_size.py` (копия) | бюджет `AGENTS.md` (64 КБ суммарно) | процесс | пишет @LM |
| `check_nul.py` (только XDiary) | маркеры конфликта `<<<<<<<`/`=======`/`>>>>>>>` | git-инциденты | пишет @LM |
| `check_canon.py` (копия) | канон ADR/ранбук (гейт ↔ деплой воспроизводимость) | архитектура | пишет @LM; ревью @AR |
| `check_adr.py` / `check_adr_refs.py` / `check_adr_status.py` | целостность ADR (статус/ссылки) | архитектура | пишет @LM; ревью @AR |
| `check_pdr.py` (копия) | целостность PDR-драфтов | процесс | пишет @LM |
| `check_rule_docs.py` (копия) | пути/токены/force-push в доках правил | процесс | пишет @LM; ревью @DO |
| `check_edm.py` (только XDiary) | соответствие EDM-канону (`edm-canon.md`) | фреймворк DTF/EDM | пишет @LM; ревью @DR |
| `check_dbcontext_options_order.py` (копия) | порядок опций DbContext | код @BE | пишет @LM; ревью @TL/@DR |
| `check_eventstore_ready.py` / `check_eventstore_registration.py` | готовность/регистрация EventStore | инфра @DO | пишет @LM; ревью @DO |
| `check_readstore_*` (`ddl`/`live`/`mapping`) | read-модель ↔ DDL/колонки | код @BE | пишет @LM; ревью @TL/@DR |
| `check_openapi.py` (копия) | OpenAPI-спеки консистентны | API | пишет @LM; ревью @TL |
| `check_api_index.py` / `check_api_calls.py` | реестр API ↔ реальные вызовы | API | пишет @LM |
| `check_apim_*` (`drift`/`health`/`parity`) | APIM: дрейф/здоровье/паритет | инфра @DO | пишет @LM; ревью @DO |
| `check_lock.py` | images.lock ↔ git/реестр | деплой | пишет @LM; ревью @DO |
| `check_env_drift.py` / `check_env_hosts.py` | дрейф окружений/hosts | инфра @DO | пишет @LM; ревью @DO |
| `check_ports.py` / `check_public_entry.py` | порты/публичные входы | инфра @DO | пишет @LM |
| `check_service_auth.py` | авторизация сервисов | код @BE | пишет @LM |
| `check_backup_freshness.py` | свежесть бэкапов | инфра @DO | пишет @LM |
| `check_keepalive.py` | keepalive-метрики | инфра @DO | пишет @LM |
| `check_probe_freshness.py` | свежесть проб | тесты @TE | пишет @LM |
| `check_test_isolation.py` / `check_test_map.py` (копия) / `check_tests_build.py` | изоляция/карта/сборка тестов | тесты @TE | пишет @LM; ревью @TE |
| `check_workflow_paths.py` (копия) / `check_workflow_shape.py` / `check_yaml_dups.py` | целостность workflow (пути/форма/дубли) | CI | пишет @LM; ревью @DO |
| `check_null_paging.py` | null-пейджинг | код @BE | пишет @LM |
| `check_schema.py` | схема БД ↔ миграции | код @BE | пишет @LM |
| `check_awaits.py` | незакрытые «жду» | процесс | пишет @LM |
| `check_front_e2e.py` | маркер front-e2e | тесты @TE | пишет @LM |
| `what_triggers.py` (копия) | показывает, какой workflow что триггерит | CI | пишет @LM |

### 2.3 Задачи, статусы, статистика

| Инструмент | Что делает | Процесс | Роли |
|---|---|---|---|
| `stats.py` (копия) | таблица статусов → README (`--write`), гейт `--check` | `stats.yml` | пишет @LM; использует все |
| `status_lint.py` (копия) | синонимы статусов (Открыта/Готово и т.п.) → rc=1 | `status-lint.yml` | пишет @LM |
| `migrate_statuses.py` (копия) | массовая миграция статусов | вручную | пишет @LM |
| `sync_board.py` | задачи `.md` → GitHub Issues + Projects | `sync-board.yml` (PROJECT_PAT `issues:write`) | пишет @LM; ревью @DO |
| `sweep.py` (копия) | «стоит без движения / обещано не начато» → issue-сигнал | `sweep.yml` (раз в час) | пишет @LM |
| `wip.py` (копия) | WIP-счётчики | `sweep`/вручную | пишет @LM |
| `whip.py` (только XDiary) | кнут простоя: 80% ролей без коммита > 30 мин → пинг `@TL + @EX` (экспоненц. задержка 30→60→120) | `whip.yml` (15 мин) | пишет @LM |
| `review_whip.py` (копия) | кнут WIP-лимита «На проверке» > 5 | `review-whip.yml` | пишет @LM |
| `commit_whip.py` (копия) | кнут прямого push/нарушений коммита | вручную/guard | пишет @LM |
| `cleanup_board.py` / `cleanup_orphans.py` | чистка доски/осиротевших веток | вручную | пишет @LM |

### 2.4 Git, зоны, работа ролей

| Инструмент | Что делает | Процесс | Роли |
|---|---|---|---|
| `git_sync.py` (копия) | wip-коммит → fetch → merge → push (одной командой) | роли вручную | пишет @LM; использует все роли |
| `role_worktree.py` (копия) | создаёт/обновляет `XDiary-<роль>` worktree | роли вручную | пишет @LM; использует все роли |
| `pr_from_branch.py` (только XDiary) | открывает PR из ветки роли | роли вручную | пишет @LM |
| `notify_owner.py` (только XDiary) | пинг владельцу | вручную | пишет @LM |
| `garmin_proxy.py` / `garmin_sync.py` (только XDiary) | прокси/синк Garmin | инфра @DO | пишет @DO (легаси) |

### 2.5 Секреты

| Инструмент | Что делает | Процесс | Роли |
|---|---|---|---|
| `check_secrets.py` | полный скан + `--range` (пуш) | `guard-secrets`/`secrets-scan` | пишет @LM; ревью @DO |
| `keycmp.py` (копия) | сравнение ключей без раскрытия («совпадают/нет» + sha256[:8]) | вручную | пишет @LM |

### 2.6 Деплой, релиз, инфра

| Инструмент | Что делает | Процесс | Роли |
|---|---|---|---|
| `gen_images_lock.py` | генерирует `images.lock` из git/реестра, `--check`/`--check-digests`/`--verify-registry` | `lock-sync.yml` | пишет @LM; ревью @DO |
| `release.py` / `release_build.py` | релиз/сборка | `release.yml` | пишет @DO |
| `rollout_service.py` | выкатка одного сервиса | `deploy.yml` | пишет @DO |
| `gen-hosts.py` / `gen_icons.py` / `gen_sample.py` | генерация hosts/иконок/сэмплов | вручную | пишет @DO |
| `snapshot_openapi.py` / `openapi_from_index.py` | снимок/сборка OpenAPI | `openapi-registry.yml` | пишет @LM; ревью @TL |

### 2.7 Здоровье, метрики, дашборд

| Инструмент | Что делает | Процесс | Роли |
|---|---|---|---|
| `health.py` (копия) | дашборд `HEALTH.md` + `health/history.jsonl` | `health.yml` (раз в сутки) | пишет @LM |
| `ci_metrics.py` (копия; в XDiary файл назван `ci-metrics.py`) | метрики прогонов | `ci-analytics`/вручную | пишет @LM |
| `latency.py` (копия) | p50/p95/max «через шлюз/напрямую» | вручную | пишет @LM; ревью @DO |
| `resource-usage.py` | потребление ресурсов | вручную | пишет @DO |
| `canon_per_probe.py` | канон по пробе | `canon-stability` | пишет @LM |

### 2.8 Синк инструментов

| Инструмент | Что делает | Процесс | Роли |
|---|---|---|---|
| `sync_tools.py` (копия) | сверка копий с манифестом (`--check`), пересинк (`--pull --from ../LoopMaker`), digest = sha256(body без шапки) | `guard-tools.yml` | пишет @LM |

### 2.9 Тесты самих инструментов

`test_check_secrets.py`, `test_git_sync.py`, `test_git_sync_state.py`, `test_stats_ref.py` — selftest-фикстуры, `check_secrets` их пропускает (не утечки).

---

## Слой 3 — Workflow (процессы CI, 44 шт.), по назначению

| Workflow | Что делает | Триггер | Ключевой инструмент | Ревью |
|---|---|---|---|---|
| `ci-guards.yml` | сводный гейт всех check_* (chat/secrets/статусы) | PR+push | check_chat, check_secrets, chat_lint, status_lint… | @LM/@DO |
| `guard-tools.yml` | копии инструментов не разошлись с манифестом | push `tools/**` | sync_tools --check | @LM |
| `guard-secrets.yml` / `secrets-scan.yml` | секреты (range/полный) | push + schedule | check_secrets | @DO |
| `guard-paths.yml` | роль не вышла за зону | push main | check_paths | @DO |
| `status-lint.yml` | синонимы статусов | push задачи | status_lint | @LM |
| `stats.yml` | README = задачи (автопересчёт + PR) | push задачи (без chat) | stats --write | @LM |
| `docs-budget.yml` | бюджет AGENTS.md | PR | check_docs_size | @LM |
| `sync-board.yml` | задачи → Issues/Project | push задачи | sync_board (PROJECT_PAT) | @DO |
| `sweep.yml` | простой/«обещано не начато» → сигнал | schedule (час) | sweep | @LM |
| `whip.yml` | кнут простоя ролей | schedule (15 мин) | whip | @LM |
| `review-whip.yml` | кнут WIP-лимита ревью | schedule (15 мин) | review_whip | @LM |
| `health.yml` | дашборд здоровья | schedule (сутки) | health --write | @LM |
| `lock-sync.yml` | перегенерация images.lock из реестра | push `6-msa/**`/`8-front/**` | gen_images_lock | @DO |
| `deploy.yml` | выкатка dev/prod | dispatch/push | rollout_service, gen_images_lock | @DO |
| `release.yml` | релиз (сборка всех сервисов) | dispatch | release_build | @DO |
| `ci-*.yml` (12 сервисов: `ci-journal`, `ci-partners`, `ci-catalog`, `ci-client`, `ci-daily-metrics`, `ci-garmin-gateway`, `ci-apple-health-gateway`, `ci-somatic-series`, `ci-privacy`, `ci-portability`, `ci-analytics`, `ci-api-gateway` + `ci-backend`) | сборка/тесты/Docker-образ одного BC | push `6-msa/BC-X/**` | dotnet build/test | @BE |
| `ci-front.yml` | сборка/тест фронта | push `8-front/**` | Playwright | @FE |
| `front-e2e-repeat.yml` | повтор E2E-прогона фронта | dispatch | Playwright | @TE |
| `pages.yml` | прод-фронт GitHub Pages | push | vite build | @FE |
| `openapi-registry.yml` | реестр OpenAPI | push API | snapshot_openapi | @TL |
| `apim-auth.yml` / `apim-drift.yml` / `smoke-apim.yml` | APIM-гейты/дым | push/dispatch | check_apim_* | @DO |
| `probe-watchdog.yml` / `public-probe.yml` / `read-model-check.yml` / `backup-check.yml` / `canon-stability.yml` | пробы/read-модель/бэкапы/канон | schedule | check_* | @DO/@TE |
| `pr-create.yml` | открыть PR (роль) | workflow_dispatch | pr_from_branch | @DO |
| `keepalive.yml` | keepalive-метки | schedule | check_keepalive | @DO |
| `workflow-shape.yml` / `test-partner-softdelete.yml` | форма workflow/точечный тест | push | check_workflow_shape | @DO |

---

## Приложение — точный маппинг workflow → инструменты (из парсинга .github/workflows)

| Workflow | Вызываемые `tools/*.py` (точный список) |
|---|---|
| `ci-guards.yml` | chat_lint, check_adr, check_adr_refs, check_adr_status, check_api_index, check_awaits, check_canon, check_chat_shrink, check_direct_push, check_edm, check_env_hosts, check_eventstore_registration, check_front_e2e, check_lock, check_nul, check_null_paging, check_ports, check_readstore_ddl, check_readstore_mapping, check_rule_docs, check_secrets, check_test_isolation, check_yaml_dups, gen_images_lock + test_* (4 фикстуры) |
| `guard-tools.yml` | sync_tools |
| `guard-paths.yml` | check_paths, check_workflow_paths |
| `guard-secrets.yml` / `secrets-scan.yml` | check_secrets, test_check_secrets |
| `stats.yml` | stats |
| `status-lint.yml` | status_lint |
| `docs-budget.yml` | check_adr, check_docs_size, check_pdr, check_rule_docs, check_test_map |
| `sweep.yml` | sweep |
| `whip.yml` | whip |
| `review-whip.yml` | review_whip |
| `sync-board.yml` | sync_board |
| `health.yml` | health |
| `lock-sync.yml` | gen_images_lock |
| `deploy.yml` | check_apim_parity |
| `apim-auth.yml` | check_service_auth |
| `keepalive.yml` | check_keepalive |
| `probe-watchdog.yml` | check_probe_freshness |
| `workflow-shape.yml` | check_workflow_shape |

**Не вызываются ни одним workflow (ручные/локальные/диспетчерские):**
`chat_sync` (Task Scheduler), `chat_inbox`, `chat_rotate`, `restore_chat`, `git_sync`,
`role_worktree`, `pr_from_branch`, `keycmp`, `migrate_statuses`, `commit_whip`, `wip`,
`cleanup_board`, `cleanup_orphans`, `notify_owner`, `latency`, `ci_metrics`, `resource-usage`,
`canon_per_probe`, `release`, `release_build`, `rollout_service`, `gen-hosts`, `gen_icons`,
`gen_sample`, `snapshot_openapi`, `openapi_from_index`, `check_openapi`, `check_apim_drift`,
`check_apim_health`, `check_backup_freshness`, `check_dbcontext_options_order`,
`check_env_drift`, `check_eventstore_ready`, `check_owner_chat`, `check_public_entry`,
`check_read_model_freshness`, `check_readstore_live`, `check_schema`, `check_service_auth`,
`check_status_chat`, `check_tests_build`, `garmin_proxy`, `garmin_sync`, `what_triggers`.

---

## Сводная карта процессов (схема)

```
Роль (тред DSH)
  │ пишет в свой outbox chat/<роль>.md (worktree XDiary-<роль>)
  ▼
chat_sync.py (Task Scheduler, 5с) ──копирует──▶ общий XDiary/chat/<роль>.md
  │
  ▼
Плагин (bridge.js) ──читает chat──▶ доставляет в сессию адресата (+Telegram)
  │
  ▼
git.js commitChat ──union(dedup+sort)──▶ ветка + PR ──▶ merge.js вливает (зелёный)

Задача (5-tech-backlog/<роль>/XX-*.md)
  │ коммит роли
  ▼
workflow push/PR: guard-paths + guard-secrets + status-lint + stats + docs-budget + guard-tools
  │ (required-чеки main: guard, status-lint, guard-tools, docs-budget, guard-secrets)
  ▼
merge.js / авто-мерж ──▶ main

Расписание: sweep (час) · whip (15 мин) · review-whip (15 мин) · health (сутки) · secrets-scan (ночь)
```

**Зависимость ключевых звеньев (что на чём стоит):**
- `chat_sync` → `bridge` → `git.js` → `merge.js` (доставка чата без ручной пересылки);
- `sync_tools` → манифест `tools/.sources.json` → `guard-tools` (копии не расходятся);
- `gen_images_lock` → `images.lock` → `lock-sync` → `deploy` → `lock-published` (выкатка);
- токены: классический PAT из локального хранилища — роли/бот/merge; `PROJECT_PAT` (Secrets, включая `issues:write` и `pull_requests:write`) — sync-board и PR-создание из workflow. Обновлён 2026-11-25 без раскрытия значения; сквозное доказательство — health run `45639606344` создал PR `#3399` (смержен).
