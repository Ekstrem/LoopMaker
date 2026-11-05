# Интерфейс метрики (контракт)

Метрик-тул — скрипт `tools/metrics/check_*.py`, который печатает **одну строку JSON** в stdout
и завершается кодом:

```json
{"role":"AR","metric":"adr_frozen","status":"ok","value":"3/3","detail":""}
```

- `status`: `ok` (пряник) / `violation` (кнут) / `unknown` (не измерено);
- exit-код: `0` = ok, `1` = violation, `2` = unknown;
- **`unknown` → ретрай N раз** (транзиент), только потом считать статус; `violation` не ретраится;
- **контекст замера — обязательное поле** (`ref`/`measured-on`): без него результат = `unknown`.

Аргументы: `--workbench <путь>` (локальный чекаут проекта из `projects.json`).
