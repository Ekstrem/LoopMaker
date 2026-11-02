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
import datetime
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
    return os.path.join(chat_dir, "chat"), config.get("role_titles", {}), config


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


TS_CANON = re.compile(r"^-\s*\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]")


STRICT = re.compile(
    r"^-\s*\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\]\s*→\s*@[A-Za-z]+(?:\s*[/+,;]\s*@?[A-Za-z]+)*:\s+\S"
)
# Адресаты через `/` (исторически популярно) — канон это `+`, поэтому считаем отдельно.
SLASH_ADDR = re.compile(r"^-\s*\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\]\s*→\s*[^:]*[/,;][^:]*:")


def count_tolerant(chat_dir, known_roles):
    """Сколько распознанных сообщений написано НЕ каноном (без скобок, `->`, без @).

    Парсер их понимает, поэтому доставка не страдает — но человеко-читаемость и
    будущие разборы (регексы в чужих скриптах) страдают. Считаем дрейф, не валим.
    """
    tolerant = 0
    total = 0
    slash = 0
    for name in sorted(os.listdir(chat_dir)):
        if not name.endswith(".md") or name.lower() in SKIP:
            continue
        if name[:-3].lower() not in known_roles:
            continue
        with open(os.path.join(chat_dir, name), encoding="utf-8-sig") as handle:
            for line in handle:
                stripped = line.strip()
                if not CANON.match(stripped):
                    continue
                total += 1
                if not STRICT.match(stripped):
                    tolerant += 1
                if SLASH_ADDR.match(stripped):
                    slash += 1
    return tolerant, total, slash


def scan_times(chat_dir, known_roles, now, tolerance_min=120, shift_hours=0):
    """Метки времени: в будущем (часы врут) и немонотонность внутри файла.

    Возвращает [(файл, метка, вид)], вид — "future" или "order".
    Не ошибка формата: старые файлы писались «по памяти» и уехали вперёд,
    поэтому по умолчанию это предупреждения (см. --strict-times).
    """
    future, order = [], []
    for name in sorted(os.listdir(chat_dir)):
        if not name.endswith(".md") or name.lower() in SKIP:
            continue
        role = name[:-3].lower()
        if role not in known_roles:
            continue
        prev = None
        # Проверяем только «новую шкалу»: всё, что до строки-маркера — исторический
        # легаси (метки писались «по памяти» и уехали вперёд). Нет маркера — проверяем всё.
        new_scale = False
        with open(os.path.join(chat_dir, name), encoding="utf-8-sig") as handle:
            for line in handle:
                if "метка = часы машины" in line:
                    new_scale = True
                    prev = None
                    continue
                if not new_scale:
                    continue
                m = TS_CANON.match(line.strip())
                if not m:
                    continue
                raw = m.group(1)
                try:
                    ts = datetime.datetime.strptime(raw, "%Y-%m-%d %H:%M")
                except ValueError:
                    continue
                expected = now + datetime.timedelta(hours=shift_hours)
                if ts > expected + datetime.timedelta(minutes=tolerance_min):
                    lag = ts - expected
                    future.append((name, raw, int(lag.total_seconds() // 3600)))
                if prev is not None and ts < prev:
                    order.append((name, raw, prev.strftime("%Y-%m-%d %H:%M")))
                prev = ts
    return future, order


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
    parser.add_argument("--strict-times", action="store_true",
                        help="метки в будущем и немонотонные — ошибка (по умолчанию предупреждение)")
    parser.add_argument("--next-stamp", action="store_true",
                        help="напечатать метку для новой строки (часы машины + сдвиг проекта)")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if args.next_stamp:
        chat_dir0, _, cfg0 = load_config(args.config)
        shift0 = int((cfg0.get("chat_time") or {}).get("shift_hours", 0))
        print((datetime.datetime.now() + datetime.timedelta(hours=shift0))
              .strftime("%Y-%m-%d %H:%M"))
        return 0

    chat_dir, titles, cfg = load_config(args.config)
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

    shift = int((cfg.get("chat_time") or {}).get("shift_hours", 0))
    tolerance = int((cfg.get("chat_time") or {}).get("tolerance_min", 120))
    future, order = scan_times(chat_dir, set(titles.keys()), datetime.datetime.now(),
                               tolerance, shift)

    if args.report or args.check:
        print("нераспознанных строк: %d (новых: %d)" % (len(problems), len(fresh)))
        for name, role, line in problems[:20]:
            print("  %s [%s]: %s" % (name, role, line[:120]))
        tolerant, total_msgs, slash = count_tolerant(chat_dir, set(titles.keys()))
        if tolerant:
            print("формат: %d из %d сообщений не каноном (без скобок, `->` или без `@`) —"
                  " распознаются, но пишем канон `- [YYYY-MM-DD HH:mm] → @role: текст`"
                  % (tolerant, total_msgs))
        if slash:
            print("адресаты: %d сообщений через `/`/`,` — канон `+`; мост теперь понимает и `/`, "
                  "но чужие регексы — нет" % slash)
        if future or order:
            print("метки времени: впереди шкалы %d (сдвиг %d ч, допуск %d мин), порядок нарушен %d"
              % (len(future), shift, tolerance, len(order)))
            for name, raw, days in future[:5]:
                print("  %s: %s — на %d дн. впереди часов машины" % (name, raw, days))
            for name, raw, prev in order[:5]:
                print("  %s: %s после %s — порядок нарушен" % (name, raw, prev))
        bad = problems or ((future or order) and args.strict_times)
        return 1 if bad and args.check else 0

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
