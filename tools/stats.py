#!/usr/bin/env python3
"""Таблица статусов задач для README (по loop.config.json).

Источник истины — поле `**Статус:**` в `<backlog>/<роль>/*.md`.
Допустимые значения берутся из конфига (по умолчанию: Открыта, В работе,
На проверке, Закрыта, Отклонена).

    python tools/stats.py            # напечатать таблицу
    python tools/stats.py --write    # обновить блок в README между маркерами
    python tools/stats.py --check    # код 1, если README отстал (для CI)
"""
import argparse
import json
import os
import re
import subprocess
import sys

CONFIG = "loop.config.json"
STATUS_RE = re.compile(r"Статус:\*\*\s*(.+)")


def repo_root():
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True)
        if out.returncode == 0:
            return out.stdout.decode("utf-8", "ignore").strip()
    except Exception:
        pass
    return os.getcwd()


def load_config(path=None):
    path = path or os.path.join(repo_root(), CONFIG)
    if not os.path.exists(path):
        sys.exit("stats: нет %s — это карта проекта (см. AGENTS.md §8)" % CONFIG)
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def pick(statuses, word, default):
    for st in statuses:
        if word in st.lower():
            return st
    return default


def normalize(raw, statuses):
    """«Выполнена»/«Готово»/«Done» -> статус закрытия из конфига."""
    s = raw.strip().lower()
    closed = pick(statuses, "закрыт", statuses[-1])
    rejected = pick(statuses, "отклон", statuses[-1])
    review = pick(statuses, "проверк", closed)
    work = pick(statuses, "работе", closed)
    if "отклон" in s:
        return rejected
    if "закрыт" in s or "выполн" in s or "готов" in s or "done" in s:
        return closed
    if "на проверке" in s or "проверк" in s:
        return review
    if "в работе" in s or "работе" in s:
        return work
    return statuses[0]


def task_status(path, statuses):
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            m = STATUS_RE.search(line)
            if m:
                return normalize(m.group(1), statuses)
    return statuses[0]


def collect(cfg):
    statuses = cfg.get("statuses") or ["Открыта", "В работе", "На проверке", "Закрыта"]
    backlog = cfg.get("backlog", "5-backlog")
    rows, totals = [], {s: 0 for s in statuses}
    for item in cfg.get("task_folders", []):
        folder = item["folder"] if isinstance(item, dict) else item
        name = item.get("title", folder) if isinstance(item, dict) else folder
        base = os.path.join(backlog, folder)
        if not os.path.isdir(base):
            continue
        counts = {s: 0 for s in statuses}
        total = 0
        for fn in sorted(os.listdir(base)):
            if not fn.endswith(".md") or fn == "README.md":
                continue
            total += 1
            counts[task_status(os.path.join(base, fn), statuses)] += 1
        rows.append((name, total, counts))
        for s in statuses:
            totals[s] += counts[s]
    return statuses, rows, totals


def render(statuses, rows, totals):
    out = ["| Роль | Всего | " + " | ".join(statuses) + " |",
           "|---" * (len(statuses) + 2) + "|"]
    for name, total, counts in rows:
        out.append("| %s | %d | %s |" % (name, total, " | ".join(str(counts[s]) for s in statuses)))
    out.append("| **Итого** | **%d** | %s |"
               % (sum(t for _, t, _ in rows),
                  " | ".join("**%d**" % totals[s] for s in statuses)))
    return out


def read_block(cfg):
    readme = (cfg.get("readme") or {}).get("file", "README.md")
    start = (cfg.get("readme") or {}).get("start", "<!-- stats:start -->")
    end = (cfg.get("readme") or {}).get("end", "<!-- stats:end -->")
    with open(readme, encoding="utf-8-sig", newline="") as f:
        text = f.read().replace("\r\n", "\n")
    if start not in text or end not in text:
        sys.exit("%s: нет маркеров %s ... %s" % (readme, start, end))
    return text, start, end, text.split(start, 1)[1].split(end, 1)[0].strip("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="обновить блок в README")
    ap.add_argument("--check", action="store_true", help="проверить, что README актуален")
    ap.add_argument("--config", default=None)
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cfg = load_config(a.config)
    if not os.path.isdir(cfg.get("backlog", "5-backlog")):
        print("stats: папки %s нет — петля ещё не заведена" % cfg.get("backlog"))
        return 0

    statuses, rows, totals = collect(cfg)
    table = render(statuses, rows, totals)

    if not rows:
        print("stats: в %s нет папок задач из конфига" % cfg.get("backlog"))
        return 0

    text, start, end, body = read_block(cfg)

    if a.check:
        if body != "\n".join(table):
            print("README устарел: статусы задач изменились.")
            print("Починить: python tools/stats.py --write")
            print("")
            print("\n".join(table))
            return 1
        print("stats: README актуален")
        return 0

    if a.write:
        readme = (cfg.get("readme") or {}).get("file", "README.md")
        new = text.split(start, 1)[0] + start + "\n" + "\n".join(table) + "\n" + end \
            + text.split(end, 1)[1]
        with open(readme, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
        print("stats: %s обновлён" % readme)
        print("\n".join(table))
        return 0

    print("\n".join(table))
    return 0


if __name__ == "__main__":
    sys.exit(main())
