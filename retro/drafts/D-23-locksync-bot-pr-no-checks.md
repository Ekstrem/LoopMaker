# Draft: lock-sync бот-PR нельзя смержить (GITHUB_TOKEN → нет чеков)

**Найдено:** @TL (2026-11-19 01:55), после включения `enforce_admins: true`
**Статус:** драфт до ретро

## Факт

PR-ы `lock-sync` создаёт бот (`app/github-actions`) через **`GITHUB_TOKEN`** ⇒ GitHub
не запускает workflows на PR от самого себя ⇒ required-чеки (`guard`, `status-lint`,
`guard-tools`, `docs-budget`, `guard-secrets`) **не появляются** ⇒ мерж невозможен даже
`--admin` (`5 of 5 required status checks are expected`). Проверено на `#2836`/`#2843`.

Раньше это пряталось за `--admin`-bypass (бот вливал без чеков молча). После
`enforce_admins: true` (владелец, 2026-11-19) дыра вскрылась: lock встал, deploy ставит
старый образ, `POST /clients` = 502.

## Варианты

- **(а)** `lock-sync` создаёт PR через **PAT** (секрет в GitHub Secrets) — тогда workflows
  запускаются, чеки появляются, мерж штатный. Рекомендация @LM: **(а)**.
- **(б)** разрешить админ-мерж bot-файла по документированному правилу (ослабляет
  `enforce_admins`, не рекомендуется).
- **(в)** lock становится частью обычного коммита роли (теряет автоматику).

## Связь

Это и есть «chicken-and-egg», предсказанный при включении `enforce_admins`: бот-PR без
чеков раньше вливался bypass, теперь честно застрял. Лечение — не ослаблять гейт, а дать
боту нормальный токен (а).

## Зона

`.github/workflows/lock-sync.yml` — @DO; PAT в Secrets — владелец.
