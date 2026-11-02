#!/usr/bin/env python3
"""Миграция значений статуса в задачах. По умолчанию — dry-run.

Нужна при смене словаря статусов (например, `Открыта → ToDo`). Меняет **только**
значение поля `**Статус:**`, сохраняя хвост в скобках: «Закрыта (TL проверил)» остаётся.

    python tools/migrate_statuses.py                       # dry-run, показать план
    python tools/migrate_statuses.py --write               # применить
    python tools/migrate_statuses.py --map "Открыта=>ToDo,Готово=>Закрыта" --write
    python tools/migrate_statuses.py --root <dir> --config loop.config.json

Защита: целевой статус должен быть в `loop.config.json` → `statuses`, иначе отказ
(обойти — `--force`). После миграции прогнать `tools/status_lint.py`.
"""
import argparse
import json
import os
import re
import sys

CONFIG = "loop.config.json"
TASK_ID = re.compile(r"^(?:[A-Z]{2,3})-\d{2,4}\.md$")
STATUS_LINE = re.compile(r"^(\s*-\s*\*\*Статус:\*\*\s*)([^(\n]+?)(\s*(?:\(.*\))?\s*)$")

DEFAULT_MAP = {
    "Открыта": "ToDo",
    "Выполнена": "Закрыта",
    "Выполнено": "Закрыта",
    "Готово": "Закрыта",
    "Done": "Закрыта",
    "Пауза": "В паузе",
}


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


def task_files(root, cfg):
    backlog = cfg.get("backlog") or "5-backlog"
    folders = [f.get("folder") for f in (cfg.get("task_folders") or []) if f.get("folder")]
    base_all = os.path.join(root, backlog)
    if not folders and os.path.isdir(base_all):
        folders = [d for d in sorted(os.listdir(base_all))
                   if os.path.isdir(os.path.join(base_all, d))]
    out = []
    for folder in folders:
        base = os.path.join(base_all, folder)
        if not os.path.isdir(base):
            continue
        for fn in sorted(os.listdir(base)):
            if TASK_ID.match(fn):
                out.append(os.path.join(base, fn))
    return out


def parse_map(raw):
    if not raw:
        return dict(DEFAULT_MAP)
    mapping = {}
    for pair in raw.split(","):
        if "=>" not in pair:
            sys.exit("migrate_statuses: ожидается «из=>в», получено: %r" % pair)
        a, b = pair.split("=>", 1)
        mapping[a.strip()] = b.strip()
    return mapping


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Миграция статусов в задачах")
    ap.add_argument("--root", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--map", default=None, help="«из=>в,из=>в»")
    ap.add_argument("--write", action="store_true", help="без этого флага — только показать")
    ap.add_argument("--force", action="store_true", help="игнорировать проверку списка статусов")
    args = ap.parse_args()

    root = repo_root(args.root)
    cfg = load_config(root, args.config)
    allowed = cfg.get("statuses") or []
    mapping = parse_map(args.map)

    if allowed and not args.force:
        bad = sorted({v for v in mapping.values() if v not in allowed})
        if bad:
            sys.exit("migrate_statuses: целевых статусов нет в конфиге: %s "
                     "(поправьте конфиг или --force)" % ", ".join(bad))

    planned, changed = [], 0
    for path in task_files(root, cfg):
        rel = os.path.relpath(path, root).replace("\\", "/")
        with open(path, encoding="utf-8-sig") as f:
            lines = f.readlines()
        touched = False
        for i, line in enumerate(lines):
            m = STATUS_LINE.match(line)
            if not m:
                continue
            cur = m.group(2).strip()
            new = mapping.get(cur)
            if not new or new == cur:
                break
            lines[i] = m.group(1) + new + m.group(3)
            planned.append("%s: %s → %s" % (rel, cur, new))
            touched = True
            break
        if touched:
            changed += 1
            if args.write:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.writelines(lines)

    for row in planned:
        print(("  ✓ " if args.write else "  · ") + row)
    print("\n%s: файлов затронуто %d" % ("ПРИМЕНЕНО" if args.write else "dry-run", changed))
    if not args.write and changed:
        print("повторите с --write, чтобы применить; затем прогоните tools/status_lint.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
