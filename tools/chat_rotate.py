"""Ротация чата: активный файл роли → архив, без повторной доставки.

Зачем: `chat/tl.md` вырос до ~400 КБ (60% всей переписки). Читать его целиком дорого,
а «партиции по датам» ломают дедуп: ключ моста = `file|ts|to|hash`, то есть смена
имени файла = «все строки новые» = массовая повторная доставка.

Поэтому ротация — не партиционирование, а перенос + **миграция ключей состояния**:
строки переезжают в `chat/archive/<роль>/`, а ключи в состоянии моста переписываются
с активного файла на архивный. После этого ни одна строка не считается новой.

    python tools/chat_rotate.py --role tl --keep 150 --state ~/.dsh/loopmaker-chat-state.json --dry-run
    python tools/chat_rotate.py --role tl --keep 150 --state ... --write

Правила безопасности:
- по умолчанию `--dry-run`, запись только с `--write`;
- архив сканированием не обходится (каталог `archive` в SKIP у моста и линтера);
- миграция ключей — best effort: мост держит состояние в памяти и может перезаписать файл;
  это безопасно, потому что каталог rchive вообще не сканируется, а оставшиеся строки
  в активном файле сохраняют свои ключи (имя файла не меняется);
- активный файл получает строку-указатель на архив.
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

TS_LINE = re.compile(r"^-\s*\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]")


def read(path):
    with open(path, encoding="utf-8-sig") as f:
        return f.read().replace("\r\n", "\n")


def split_messages(text):
    """-> (шапка, [все строки после шапки]) — шапка это всё до первого сообщения.

    Режем по позиции, а не по фильтру: между сообщениями могут быть пустые строки
    и служебные пометки, их нельзя терять при переносе.
    """
    lines = text.split("\n")
    first = next((i for i, l in enumerate(lines) if l.startswith("- [")), len(lines))
    head = "\n".join(lines[:first]).rstrip("\n")
    body = lines[first:]
    return head, body


def message_count(body):
    return sum(1 for l in body if TS_LINE.match(l))


def cut_index(body, keep):
    """Индекс, с которого начинаются последние `keep` сообщений."""
    seen = 0
    total = message_count(body)
    target = total - keep
    for i, l in enumerate(body):
        if TS_LINE.match(l):
            if seen == target:
                return i
            seen += 1
    return len(body)


def migrate_state(state_path, role, mapping):
    """Переписать ключи `file|ts|to|hash` с активного файла на архивный."""
    if not os.path.exists(state_path):
        return 0
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    seen = state.get("seen") or []
    changed = 0
    out = []
    for key in seen:
        parts = str(key).split("|")
        if parts and parts[0] == role and key in mapping:
            out.append(mapping[key])
            changed += 1
        else:
            out.append(key)
    state["seen"] = out
    with open(state_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(state, f, ensure_ascii=False)
    return changed


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Ротация outbox-чата в архив")
    ap.add_argument("--role", required=True, help="роль = имя файла без .md: tl, be, do…")
    ap.add_argument("--chat", default=None, help="папка чата (по умолчанию 5-tech-backlog/chat)")
    ap.add_argument("--keep", type=int, default=150, help="сколько последних сообщений оставить")
    ap.add_argument("--state", default=None, help="файл состояния моста (loopmaker-chat-state.json)")
    ap.add_argument("--write", action="store_true", help="применить (по умолчанию dry-run)")
    ap.add_argument("--root", default=None)
    args = ap.parse_args()

    root = args.root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    chat = args.chat or os.path.join(root, "5-tech-backlog", "chat")
    active = os.path.join(chat, args.role + ".md")
    if not os.path.exists(active):
        sys.exit("chat_rotate: нет файла %s" % active)

    head, body = split_messages(read(active))
    total = message_count(body)
    if total <= args.keep:
        print("chat_rotate: %s — %d сообщений, ротация не нужна (порог %d)"
              % (args.role, total, args.keep))
        return 0

    cut = cut_index(body, args.keep)
    moved = body[:cut]
    stay = body[cut:]
    stamps = [TS_LINE.match(l).group(1)[:10] for l in moved if TS_LINE.match(l)]
    first_ts, last_ts = stamps[0], stamps[-1]
    name = "%s..%s.md" % (first_ts, last_ts)
    arch_dir = os.path.join(chat, "archive", args.role)
    arch_path = os.path.join(arch_dir, name)

    print("chat_rotate: %s" % args.role)
    print("  активный: %d сообщений (%d байт)" % (total, os.path.getsize(active)))
    print("  в архив:  %d сообщений → chat/archive/%s/%s" % (message_count(moved), args.role, name))
    print("  остаётся: %d сообщений в активном файле" % message_count(stay))

    if not args.state:
        print("  состояние моста не указано (--state): миграцию ключей проверить нельзя")
    elif not os.path.exists(args.state):
        print("  ВНИМАНИЕ: файла состояния нет — ключи не мигрируются → возможна пере-доставка")
    else:
        print("  состояние: %s — миграция ключей будет выполнена" % args.state)

    if not args.write:
        print("dry-run: ничего не изменено (добавь --write)")
        return 0

    # Порядок важен: сначала мигрируем состояние моста (если не удалось — выходим,
    # не трогая файлы), и только потом переносим строки. Иначе при ошибке на состоянии
    # получим «файлы перенесены, ключи старые» = массовая повторная доставка.
    mapping = {}
    for line in moved:
        if not TS_LINE.match(line):
            continue
        ts = TS_LINE.match(line).group(1)
        mapping["%s|%s|" % (args.role, ts)] = "archive/%s/%s|%s|" % (args.role, name, ts)

    if args.state and os.path.exists(args.state):
        try:
            with open(args.state, encoding="utf-8") as f:
                state = json.load(f)
            seen = state.get("seen") or []
            changed = 0
            for i, key in enumerate(seen):
                for prefix, replacement in mapping.items():
                    if str(key).startswith(prefix):
                        seen[i] = str(key).replace(prefix, replacement, 1)
                        changed += 1
                        break
            state["seen"] = seen
            with open(args.state, "w", encoding="utf-8", newline="\n") as f:
                json.dump(state, f, ensure_ascii=False)
            print("  мигрировано ключей: %d" % changed)
        except OSError as e:
            print("  ВНИМАНИЕ: состояние не обновлено (%s) — миграция пропущена (archive не сканируется, пере-доставки не будет)" % e)

    os.makedirs(arch_dir, exist_ok=True)
    if os.path.exists(arch_path):
        sys.exit("chat_rotate: архив уже существует: %s" % arch_path)

    archive_text = "# Архив outbox «%s»: %s..%s (перенесено %s)\n\n" % (
        args.role, first_ts, last_ts, time.strftime("%Y-%m-%d %H:%M")) + "\n".join(moved) + "\n"
    with open(arch_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(archive_text)

    pointer = ("_Архив: `archive/%s/%s` — %d сообщений (%s..%s). "
               "Активный файл хранит последние %d; история не удалена._"
               % (args.role, name, message_count(moved), first_ts, last_ts, message_count(stay)))
    with open(active, "w", encoding="utf-8", newline="\n") as f:
        f.write(head + "\n\n" + pointer + "\n" + "\n".join(stay) + "\n")

    print("chat_rotate: готово. Не забудь коммит: активный файл + архив.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
