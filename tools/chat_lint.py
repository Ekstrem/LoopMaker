#!/usr/bin/env python3
"""Линтер чата: находит строки, которые парсер не понял, и пишет автору.

Зачем: формат строки чата — контракт трёх потребителей (`lib/parse.js`,
`tools/merge_chat.py`, `tools/chat_bot.py`). Если роль напишет адресата не так
(`→ @DO + TE:`, без двоеточия, без времени), строка **молча** выпадает: ни
доставки, ни сводки, ни ошибки. Реальный случай 03.11: 6 сообщений с двумя
адресатами не дошли, и это заметил человек, а не система.

Что делает:
  1. читает `<backlog>/chat/*.md` (кроме README/_dialogue);
  2. ищет строки-кандидаты (начинаются с `-` и содержат стрелку), которые
     не разбираются каноническим регексом;
  3. пишет замечание в `chat/_lint.md` строкой, адресованной автору:
     `- [2026-11-03 18:00] → @BE: ⚠️ строка не распознана — …`
  4. помнит уже отправленные замечания (`.lint-state.json`), чтобы не спамить.

`chat/_lint.md` читает мост `dsh-loopmaker`: файлы на `_` он считает служебными
(автор — LoopMaker), поэтому замечание приходит автору в его сессию.

Запуск:
    python tools/chat_lint.py            # написать замечания и отчитаться
    python tools/chat_lint.py --report   # только показать, ничего не писать
    python tools/chat_lint.py --check    # код 1, если есть нераспознанные строки (CI)
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LINT_FILE = "_lint.md"
STATE_FILE = ".lint-state.json"
SKIP = {"readme.md", "_dialogue.md", LINT_FILE.lower()}

# Канон и терпимость — как в lib/parse.js: скобки или время без скобок,
# стрелка любая, несколько адресатов через `+`.
CANON = re.compile(
    r"^-\s*(?:\[([^\]]+)\]|(\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}))"
    r"\s*(?:→|->|=>)\s*@?([^:]+?)\s*:\s*(.+)$"
)
# «похоже на попытку сообщения»: строка списка со стрелкой
# Строка-кандидат начинается ровно с первого столбца: строки в чате не отбивают
# пробелами, а маркдаун-списки внутри документов — отбивают.
CANDIDATE = re.compile(r"^-[ \t]+(?:\[|\d{4}-\d{2}-\d{2}).*(?:→|->|=>)")
# Так отсекаются прозаические списки вроде `- `dev` (VPS-2): cron … → …`: у
# сообщения чата всегда есть время в начале строки.
FENCE = re.compile(r"^\s*(```|~~~)")


def load_config(path):
    with open(path, encoding="utf-8-sig") as handle:
        config = json.load(handle)
    backlog = config.get("backlog", "5-backlog")
    # Путь к бэклогу — относительно самого конфига, а не текущей папки.
    base = os.path.dirname(os.path.abspath(path))
    chat_dir = backlog if os.path.isabs(backlog) else os.path.join(base, backlog)
    return os.path.join(chat_dir, "chat"), config.get("role_titles", {})


def load_state(chat_dir):
    path = os.path.join(chat_dir, STATE_FILE)
    try:
        with open(path, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"sent": []}
    if not isinstance(data.get("sent"), list):
        data["sent"] = []
    return data


def save_state(chat_dir, state):
    path = os.path.join(chat_dir, STATE_FILE)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    os.replace(temp, path)


def digest(text):
    value = 5381
    for char in text:
        value = ((value << 5) + value + ord(char)) & 0xFFFFFFFF
    return format(value, "x")


def scan(chat_dir, known_roles):
    """-> [(файл, роль, строка)] — нераспознанные кандидаты вне код-блоков."""
    problems = []
    for name in sorted(os.listdir(chat_dir)):
        if not name.endswith(".md") or name.lower() in SKIP:
            continue
        role = name[:-3].lower()
        # Только файлы ролей: TODO-*.md, заметки и прочее — не outbox.
        if role not in known_roles:
            continue
        in_fence = False
        with open(os.path.join(chat_dir, name), encoding="utf-8-sig") as handle:
            for line in handle:
                if FENCE.match(line):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    continue
                if not CANDIDATE.match(line.strip()):
                    continue
                if CANON.match(line.strip()):
                    continue
                problems.append((name, role, line.strip()))
    return problems


def notice(role, titles, line, now):
    author = titles.get(role, role.upper())
    short = line if len(line) <= 160 else line[:157] + "..."
    return (
        "- [%s] → @%s: ⚠️ строка не распознана как сообщение, тебе нужно переписать её. "
        "Формат: `- [YYYY-MM-DD HH:mm] → @КОМУ: текст` (адресат — роль через `@`, "
        "несколько адресатов через `+`). Твоя строка: %s"
        % (now, author.upper(), short)
    )


def main():
    parser = argparse.ArgumentParser(description="Линтер формата чата ролей")
    parser.add_argument("--config", default=os.path.join(HERE, "..", "loop.config.json"))
    parser.add_argument("--report", action="store_true", help="только показать")
    parser.add_argument("--check", action="store_true", help="код 1 при находках (CI)")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    chat_dir, titles = load_config(args.config)
    if not os.path.isdir(chat_dir):
        sys.exit("chat_lint: нет папки %s" % chat_dir)

    problems = scan(chat_dir, set(titles.keys()))
    state = load_state(chat_dir)
    sent = set(state["sent"])

    fresh = []
    for name, role, line in problems:
        key = "%s|%s" % (name, digest(line))
        if key in sent:
            continue
        fresh.append((key, role, line))

    if args.report or args.check:
        print("нераспознанных строк: %d (новых: %d)" % (len(problems), len(fresh)))
        for name, role, line in problems[:20]:
            print("  %s [%s]: %s" % (name, role, line[:120]))
        return 1 if problems and args.check else 0

    if not fresh:
        print("chat_lint: всё чисто, нераспознанных строк нет")
        return 0

    now = time.strftime("%Y-%m-%d %H:%M")
    path = os.path.join(chat_dir, LINT_FILE)
    header = "# Служебный файл линтера: замечания авторам (пишет tools/chat_lint.py)\n\n"
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        if not exists:
            handle.write(header)
        for key, role, line in fresh:
            handle.write(notice(role, titles, line, now) + "\n")
            state["sent"].append(key)

    state["sent"] = state["sent"][-2000:]
    save_state(chat_dir, state)
    print("chat_lint: замечаний записано %d -> %s" % (len(fresh), path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
