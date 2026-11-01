#!/usr/bin/env python3
"""Собрать диалог ролей: <backlog>/chat/*.md (outbox) -> chat/_dialogue.md.

Outbox-файлы — источник правды (каждая роль пишет только в свой файл).
Скрипт только читает их и складывает сообщения в один файл, отсортированный
по времени. Сгенерированный файл в .gitignore — руками не правим.

    python tools/merge_chat.py
    python tools/merge_chat.py --out путь/к/сводке.md
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

CONFIG = "loop.config.json"
# - [2026-10-30 15:20] → @FE: текст
MSG = re.compile(r"^-\s*(?:\[([^\]]+)\]|(\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}))\s*(?:→|->|=>)\s*@?([^:]+?)\s*:\s*(.+)$")
SKIP = {"README.md", "_dialogue.md"}


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
        sys.exit("merge_chat: нет %s" % CONFIG)
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--config", default=None)
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cfg = load_config(a.config)
    chat_dir = os.path.join(cfg.get("backlog", "5-backlog"), "chat")
    titles = cfg.get("role_titles", {})
    out_path = a.out or os.path.join(chat_dir, "_dialogue.md")

    if not os.path.isdir(chat_dir):
        sys.exit("merge_chat: нет папки %s" % chat_dir)

    messages = []
    for path in sorted(glob.glob(os.path.join(chat_dir, "*.md"))):
        fn = os.path.basename(path)
        if fn in SKIP:
            continue
        role = fn[:-3].lower()
        src = titles.get(role, role.upper())
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                m = MSG.match(line.strip())
                if m:
                    ts = (m.group(1) or m.group(2) or "").strip()
                    messages.append((ts, src, m.group(3).strip(), m.group(4).strip()))
    messages.sort(key=lambda x: x[0])

    lines = [
        "# Диалог ролей (авто-сводка)",
        "",
        "> Генерируется из `chat/*.md` (outbox). **Не редактировать вручную** — "
        "правки перезапишутся. Пиши в файл своей роли.",
        "",
        "| Время | От | Кому | Сообщение |",
        "|---|---|---|---|",
    ]
    for ts, src, to, text in messages:
        lines.append("| %s | %s | @%s | %s |" % (ts, src, to, text.replace("|", "\\|")))
    if not messages:
        lines.append("| — | — | — | Сообщений пока нет |")

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("merge_chat: %d сообщений -> %s" % (len(messages), out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
