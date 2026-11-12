#!/usr/bin/env python3
"""Кнут за WIP-лимит ревью (владелец 2026-11-13, PDR-047).

Считает задачи в «На проверке». Если их больше лимита — координатор @TL получает
кнут: «разберись со связями и запуши команду», а не копи ревью дальше.

Правило: взял задачу — доводи до конца; накопил ревью — разгребай, а не открывай
новое. Лимит задаётся в `loop.config.json` → `wip.review_limit` (по умолчанию 5).

rc=0 — в пределах лимита; rc=1 — лимит нарушен (кнут дописан в `chat/lm.md`).
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

TASK_ID = re.compile(r"^[A-Z]{2,3}-\d{2,4}\.md$")
IN_REVIEW = re.compile(r"\*\*Статус:?\*\*\s*(?:На проверке)\b")


def main():
    ap = argparse.ArgumentParser(description="Кнут за WIP-лимит ревью")
    ap.add_argument("--config", default="loop.config.json")
    ap.add_argument("--root", default=".")
    args = ap.parse_args()

    root = args.root
    cfg = {}
    cfg_path = os.path.join(root, args.config)
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8-sig") as f:
            cfg = json.load(f)

    backlog = cfg.get("backlog") or "5-tech-backlog"
    wip = cfg.get("wip") or {}
    limit = int(wip.get("review_limit", 5))

    base = os.path.join(root, backlog)
    review = []
    for dirpath, _dirs, files in os.walk(base):
        if "/chat" in dirpath.replace(os.sep, "/"):
            continue
        for fn in files:
            if not TASK_ID.match(fn):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, encoding="utf-8-sig") as f:
                    text = f.read()
            except OSError:
                continue
            if IN_REVIEW.search(text):
                review.append(fn[:-3])

    n = len(review)
    if n <= limit:
        print(f"review-whip: «На проверке» {n} ≤ {limit} — ок")
        return 0

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    ids = ", ".join(sorted(review))
    line = (f"- [{ts}] → @TL: 🔴 кнут за WIP-лимит — {n} задач в «На проверке» "
            f"(лимит {limit}): {ids}. Разберись со связями и запуши команду "
            f"по этим задачам — не открывай новое, пока ревью не разгребается.")
    chat = os.path.join(root, backlog, "chat", "lm.md")
    os.makedirs(os.path.dirname(chat), exist_ok=True)
    with open(chat, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(f"review-whip: «На проверке» {n} > {limit} — кнут @TL записан")
    return 1


if __name__ == "__main__":
    sys.exit(main())
