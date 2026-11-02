"""Гейт бюджета инструкций: сумма AGENTS.md ≤ 64 КиБ, каждый ≤ 16 КиБ."""
import argparse
import glob
import json
import os
import sys

CONFIG = "loop.config.json"
DEFAULT_TOTAL = 65536
DEFAULT_PER_FILE = 16384


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Бюджет инструкций (AGENTS.md)")
    ap.add_argument("--root", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--total", type=int, default=None)
    ap.add_argument("--per-file", type=int, default=None)
    args = ap.parse_args()

    root = args.root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg_path = args.config or os.path.join(root, CONFIG)
    cfg = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    budget = cfg.get("docs_budget") or {}
    total_limit = args.total or budget.get("total_bytes") or DEFAULT_TOTAL
    per_limit = args.per_file or budget.get("per_file_bytes") or DEFAULT_PER_FILE

    files = sorted(glob.glob(os.path.join(root, "**", "AGENTS.md"), recursive=True))
    rows, total = [], 0
    for path in files:
        size = os.path.getsize(path)
        total += size
        rows.append((os.path.relpath(path, root).replace("\\", "/"), size))

    print("бюджет инструкций: файлов %d, сумма %d из %d байт" % (len(rows), total, total_limit))
    for name, size in rows:
        mark = "!" if size > per_limit else " "
        print("  %s %-28s %6d байт" % (mark, name, size))

    bad = []
    if total > total_limit:
        bad.append("сумма %d > %d" % (total, total_limit))
    for name, size in rows:
        if size > per_limit:
            bad.append("%s: %d > %d" % (name, size, per_limit))

    if bad:
        print("")
        for line in bad:
            print("  превышение:", line)
        print("")
        print("Лечение: норма — в AGENTS.md (≤ %d КБ), истории и детали — в RULES.md/задачи."
              % (per_limit // 1024))
        return 1
    print("docs-budget: в пределах лимита")
    return 0


if __name__ == "__main__":
    sys.exit(main())
