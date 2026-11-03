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
    """Привести значение к канону: синонимы и «почти-статусы» -> статус из конфига."""
    s = raw.strip().lower()
    closed = pick(statuses, "закрыт", statuses[-1])
    rejected = pick(statuses, "отклон", statuses[-1])
    review = pick(statuses, "проверк", closed)
    work = pick(statuses, "работе", closed)
    pause = pick(statuses, "пауз", work)
    groom = pick(statuses, "проработ", work)
    todo = pick(statuses, "todo", work)
    if "отклон" in s:
        return rejected
    if "закрыт" in s or "выполн" in s or "готов" in s or "done" in s:
        return closed
    if "пауз" in s:
        return pause
    if "проработ" in s:
        return groom
    if "проверк" in s or "ревью" in s:
        return review
    if "todo" in s or "в очереди" in s:
        return todo
    if "работе" in s:
        return work
    return statuses[0]


def status_from_text(text, statuses):
    """Статус из содержимого файла (общий код для дерева и для git-ref)."""
    for line in text.splitlines():
        m = STATUS_RE.search(line)
        if m:
            return normalize(m.group(1), statuses)
    return statuses[0]


def ref_read(ref, path):
    """Содержимое файла из указанного git-ref или None, если файла там нет."""
    r = subprocess.run(["git", "show", "%s:%s" % (ref, path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else None


def ref_files(ref, base):
    """Список md-файлов в base/<folder> по данным git-ref."""
    r = subprocess.run(["git", "ls-tree", "-r", "--name-only", ref, "--", base],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit("stats: не могу прочитать %s из %s: %s" % (base, ref, r.stderr.strip()[:200]))
    return [p for p in r.stdout.splitlines() if p.endswith(".md")]


def task_status(path, statuses):
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            m = STATUS_RE.search(line)
            if m:
                return normalize(m.group(1), statuses)
    return statuses[0]


def collect(cfg, ref=None):
    statuses = cfg.get("statuses") or ["Открыта", "В работе", "На проверке", "Закрыта"]
    backlog = cfg.get("backlog", "5-backlog")
    rows, totals = [], {s: 0 for s in statuses}
    for item in cfg.get("task_folders", []):
        folder = item["folder"] if isinstance(item, dict) else item
        name = item.get("title", folder) if isinstance(item, dict) else folder
        base = os.path.join(backlog, folder)
        counts = {s: 0 for s in statuses}
        total = 0
        if ref:
            for path in sorted(ref_files(ref, base)):
                if path.endswith("/README.md"):
                    continue
                text = ref_read(ref, path)
                if text is None:
                    continue
                total += 1
                counts[status_from_text(text, statuses)] += 1
        else:
            if not os.path.isdir(base):
                continue
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


def read_block(cfg, ref=None):
    readme = (cfg.get("readme") or {}).get("file", "README.md")
    start = (cfg.get("readme") or {}).get("start", "<!-- stats:start -->")
    end = (cfg.get("readme") or {}).get("end", "<!-- stats:end -->")
    if ref:
        raw = ref_read(ref, readme)
        if raw is None:
            sys.exit("stats: в %s нет файла %s" % (ref, readme))
        text = raw.replace("\r\n", "\n")
    else:
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
    ap.add_argument("--ref", default=None,
                    help="считать и сверять по git-ref (например origin/main), а не по дереву; "
                         "ловит случай «конфиг/README согласованы в дереве, но оторваны от origin»")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if a.ref and a.write:
        sys.exit("stats: --write и --ref несовместимы (в git-ref писать нельзя)")

    cfg = load_config(a.config)
    if not os.path.isdir(cfg.get("backlog", "5-backlog")):
        print("stats: папки %s нет — петля ещё не заведена" % cfg.get("backlog"))
        return 0

    statuses, rows, totals = collect(cfg, ref=a.ref)
    table = render(statuses, rows, totals)

    if not rows:
        print("stats: в %s нет папок задач из конфига" % cfg.get("backlog"))
        return 0

    text, start, end, body = read_block(cfg, ref=a.ref)

    if a.check:
        if body != "\n".join(table):
            where = ("в %s" % a.ref) if a.ref else "в дереве"
            print("README устарел %s: статусы задач разошлись с таблицей." % where)
            print("Починить: python tools/stats.py --write")
            print("")
            print("\n".join(table))
            return 1
        print("stats: README актуален" + ((" (%s)" % a.ref) if a.ref else ""))
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
