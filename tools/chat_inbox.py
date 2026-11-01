#!/usr/bin/env python3
"""Гейт координатора: вопросы к @TL, на которые нет ответа в его outbox'е.

    python tools/chat_inbox.py

Код 1 — есть неотвеченные вопросы (для мониторинга координатором каждый раунд).
Формат сообщения: `- [YYYY-MM-DD HH:mm] → @КОМУ: текст`.

Эвристика: «вопрос/просьба» распознаётся по маркерам (?, прошу, подтверди, какой вариант…),
чтобы не шуметь на подтверждениях и инструкциях.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

CONFIG = "loop.config.json"
MSG = re.compile(r"^-\s*\[([^\]]+)\]\s*→\s*@?([^\s:]+)\s*:\s*(.+)$")
SKIP = {"README.md", "_dialogue.md"}

QUESTION_MARKERS = (
    "?", "прошу", "скажи", "выбери", "выберите", "уточни", "подтверди",
    "можно ли", "какой вариант", "какой из", "как быть",
)


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
        sys.exit("chat_inbox: нет %s" % CONFIG)
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def parse(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            m = MSG.match(line.strip())
            if m:
                to = m.group(2).strip()
                if not to.startswith("@"):
                    to = "@" + to
                out.append({"t": m.group(1).strip(), "to": to, "text": m.group(3).strip()})
    return out


def is_question(text):
    t = text.lower()
    return any(k in t for k in QUESTION_MARKERS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cfg = load_config(a.config)
    coordinator = cfg.get("coordinator", "tl")
    coord_title = cfg.get("role_titles", {}).get(coordinator, coordinator.upper())
    chat_dir = os.path.join(cfg.get("backlog", "5-backlog"), "chat")
    roles = [r for r in (cfg.get("zones") or {}) if r != coordinator]

    coordinator_msgs = parse(os.path.join(chat_dir, coordinator + ".md"))
    unanswered = []
    for role in roles:
        # тег адресата в чате — короткий код роли (@TE, @FE), а не её название
        tag = "@" + role.upper()
        for m in parse(os.path.join(chat_dir, role + ".md")):
            if not re.search(r"@" + coordinator.upper() + r"\b", m["to"], re.I):
                continue
            if not is_question(m["text"]):
                continue
            replied = any(
                (re.search(re.escape(tag) + r"\b", r["to"], re.I)
                 or re.search(r"@ALL\b", r["to"], re.I))
                and r["t"] > m["t"]
                for r in coordinator_msgs
            )
            if not replied:
                unanswered.append((role.upper(), m["t"], m["text"]))

    if not unanswered:
        print("chat_inbox: неотвеченных вопросов к @%s нет" % coordinator.upper())
        return 0

    print("chat_inbox: НЕ ОТВЕЧЕНО вопросов к @%s: %d" % (coordinator.upper(), len(unanswered)))
    for role, t, text in sorted(unanswered, key=lambda x: x[1]):
        print("  [%s] %s -> @%s: %s" % (t, role, coord_title, text[:200]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
