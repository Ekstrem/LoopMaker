#!/usr/bin/env python3
"""Guard: у тяжёлых workflow должен быть фильтр путей, иначе чат гоняет деплой/тесты.

Инцидент, из которого это выросло (XDiary, 2026-11-06): коммит сообщения в outbox
триггерил Pages/деплой/синк/тесты. Дорого и, хуже того, такой пуш отменял идущий
E2E (через `cancel-in-progress`).

Правило: если у workflow есть `on.push` **без** `paths` и **без** `paths-ignore` —
это ошибка. Исключение — лёгкие guard'ы, которым можно на каждый пуш; их список
берётся из `loop.config.json`:

    "ci": { "light_workflows": ["guard-paths.yml", "stats.yml", "chat-guards.yml"] }

Запуск:
    python tools/check_workflow_paths.py
    python tools/check_workflow_paths.py --config loop.config.json --root <dir>

Код возврата: 0 — все тяжёлые с фильтром, 1 — есть нарушения (или битый YAML).
Требуется PyYAML; без него — предупреждение и код 0 (чтобы не ломать чужой CI).
"""
import argparse
import glob
import json
import os
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

CONFIG = "loop.config.json"
DEFAULT_LIGHT = ["guard-paths.yml", "stats.yml", "merge-chat.yml", "chat-guards.yml"]


def repo_root(arg=None):
    if arg:
        return os.path.abspath(arg)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def load_config(root, path=None):
    path = path or os.path.join(root, CONFIG)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def push_trigger(doc):
    """Секция `on.push` или None. PyYAML 1.1 парсит ключ `on` как True."""
    if not isinstance(doc, dict):
        return None
    on = doc.get("on", doc.get(True))
    if not isinstance(on, dict):
        return None
    return on.get("push")


def main():
    ap = argparse.ArgumentParser(description="Фильтры путей у workflow")
    ap.add_argument("--root", default=None)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    if yaml is None:
        print("check_workflow_paths: PyYAML не установлен — пропускаю", file=sys.stderr)
        return 0

    root = repo_root(args.root)
    cfg = load_config(root, args.config)
    light = set((cfg.get("ci") or {}).get("light_workflows") or DEFAULT_LIGHT)

    wf_dir = os.path.join(root, ".github", "workflows")
    if not os.path.isdir(wf_dir):
        print("check_workflow_paths: нет .github/workflows — пропускаю")
        return 0

    bad = []
    for path in sorted(glob.glob(os.path.join(wf_dir, "*.yml"))):
        name = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as f:
                doc = yaml.safe_load(f)
        except Exception as e:
            print("  ! %s: не парсится (%s)" % (name, e), file=sys.stderr)
            bad.append(name)
            continue
        push = push_trigger(doc)
        if push is None:
            continue                      # dispatch-only / расписание — не наш случай
        if isinstance(push, dict) and (push.get("paths") or push.get("paths-ignore")):
            continue                      # фильтр есть
        if name in light:
            continue                      # лёгкий guard — можно на каждый пуш
        bad.append(name)

    if bad:
        for n in bad:
            print("  ! %s: on.push без paths/paths-ignore — будет гонять тяжёлое "
                  "на любой коммит (в т.ч. сообщение в чат)" % n, file=sys.stderr)
        print("check_workflow_paths: FAIL (%d)" % len(bad))
        return 1

    print("check_workflow_paths: OK (у всех тяжёлых workflow есть фильтр путей)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
