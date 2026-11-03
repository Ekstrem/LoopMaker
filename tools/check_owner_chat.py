#!/usr/bin/env python3
# ИСТОЧНИК ПРАВДЫ. Копия в проектах — tools/check_owner_chat.py (sync_tools).
"""check_owner_chat.py — «строка в файле ≠ строка в git» (D-16 + PDR-26).

Зачем: строки чата (владельца и ролей) дважды существовали только в рабочем дереве
и исчезали при следующем checkout. Плагин моста git-операции делать не должен, поэтому
проверка механическая: раннер/агент/`git_sync` зовут её после пуша.

ВАЖНО (уточнение @TL 2026-11-09 08:07): «локально есть, в origin нет» — это ДВА разных
случая, и путать их нельзя, иначе детектор даёт ложный красный:
  * `unfinished_push` — строка закоммичена, но пуш не доехал (впереди origin N коммитов;
    бывает из-за `fatal: stash failed` на грязном общем дереве). Не дефект записи.
  * `bridge_defect`  — строка живёт только в рабочем дереве и не закоммичена при чистом
    относительно origin состоянии: при следующем reset она исчезнет.
  * `sla_wait`       — строка молодая: не в origin, но и коммитить её ещё не требовалось.

Проверки:
  1. грязные файлы чата — с разделением «твоя строка не закоммичена» / «чужие правки»;
  2. каждая локальная строка должна присутствовать в `git show <ref>:<файл>`;
  3. SLA (`--sla`, по умолчанию 10 мин): старая отсутствующая строка = ошибка;
  4. §6: 6+ цифр подряд или телефон в несохранённой строке — коммитить нельзя.

Запуск:
  python tools/check_owner_chat.py             # только ex.md
  python tools/check_owner_chat.py --all       # все файлы чата
  python tools/check_owner_chat.py --selftest  # таблица решений, без git
"""
import argparse
import datetime
import os
import re
import subprocess
import sys

CHAT_DIR = "5-tech-backlog/chat"
OWNER = "ex.md"
STAMP = re.compile(r"^- \[(\d{4}-\d\d-\d\d \d\d:\d\d)\]")
CODE = re.compile(r"(?<!\d)\d{6,}(?!\d)")
PHONE = re.compile(r"\+\d{10,15}")

BRIDGE = "bridge_defect"
UNFINISHED = "unfinished_push"
SLA_WAIT = "sla_wait"


def classify(in_origin, ahead, worktree_only, dirty, in_flight, age_min, sla):
    """Один симптом — три причины. Порядок проверок и есть приоритет."""
    if in_origin:
        return "ok"
    if in_flight:
        return UNFINISHED
    if ahead > 0:
        return UNFINISHED
    # Порядок важен: свежая строка (в пределах SLA) — это ещё не дефект, даже если
    # дерево грязное: возможно, её только что записала другая роль (общее дерево).
    # Дефект — только когда SLA истёк, а строки в origin так и нет.
    if age_min > sla:
        return BRIDGE
    return SLA_WAIT


def run(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def git_dir():
    return (run("rev-parse", "--git-dir").stdout or ".git").strip()


def in_flight_state():
    gd = git_dir()
    for name in ("MERGE_HEAD", "REBASE_HEAD", "index.lock"):
        if os.path.exists(os.path.join(gd, name)):
            return True
    return False


def ahead_count(ref):
    try:
        return int((run("rev-list", "--count", "%s..HEAD" % ref).stdout or "0").strip())
    except ValueError:
        return 0


def dirty_files():
    return [l for l in run("status", "--porcelain", CHAT_DIR).stdout.splitlines() if l.strip()]


def origin_text(ref, path):
    return run("show", "%s:%s" % (ref, path)).stdout


def selftest():
    cases = [
        ((True, 3, True, True, True, 60, 10), "ok"),
        ((False, 2, False, True, False, 60, 10), UNFINISHED),
        ((False, 0, True, True, False, 5, 10), SLA_WAIT),   # свежая: ещё не дефект
        ((False, 0, True, True, False, 60, 10), BRIDGE),
        ((False, 0, False, False, False, 3, 10), SLA_WAIT),
        ((False, 0, False, False, True, 60, 10), UNFINISHED),
    ]
    bad = 0
    for args, expected in cases:
        got = classify(*args)
        ok = got == expected
        bad += 0 if ok else 1
        print("  [%s] ahead=%s worktree_only=%s dirty=%s in_flight=%s age=%s -> %s (ждали %s)"
              % ("OK " if ok else "FAIL", args[1], args[2], args[3], args[4], args[5], got, expected))
    print("check_owner_chat: selftest %s" % ("пройден" if bad == 0 else "ПРОВАЛЕН (%d)" % bad))
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--sla", type=int, default=10,
                    help="минут, после которых отсутствие в origin = дефект")
    ap.add_argument("--now", default=None, help="для тестов: 'YYYY-MM-DD HH:mm'")
    ap.add_argument("--all", action="store_true", help="проверить ВСЕ файлы чата")
    ap.add_argument("--selftest", action="store_true", help="проверка таблицы решений")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if a.selftest:
        return selftest()

    failures, warnings = [], []
    ahead = ahead_count(a.ref)
    inflight = in_flight_state()
    dirty = dirty_files()
    if dirty:
        warnings.append("в дереве правки чата (%d) — чужие незакоммиченные изменения "
                        "тоже считаются" % len(dirty))

    now = datetime.datetime.strptime(a.now, "%Y-%m-%d %H:%M") if a.now else datetime.datetime.now()
    files = sorted(n for n in os.listdir(CHAT_DIR) if n.endswith(".md")) if a.all else [OWNER]

    total_local = total_missing = 0
    for name in files:
        path = "%s/%s" % (CHAT_DIR, name)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                local = [l for l in f.read().splitlines() if STAMP.match(l)]
        except OSError:
            continue
        remote = {l for l in origin_text(a.ref, path).splitlines() if STAMP.match(l)}
        head = {l for l in run("show", "HEAD:%s" % path).stdout.splitlines() if STAMP.match(l)}
        missing = [l for l in local if l not in remote]
        total_local += len(local)
        total_missing += len(missing)
        if missing:
            warnings.append("%s: строк локально %d, в %s нет %d"
                            % (name, len(local), a.ref, len(missing)))
        for line in missing:
            m = STAMP.match(line)
            try:
                when = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
            except ValueError:
                failures.append("%s: нечитаемая метка: %s" % (name, line[:60]))
                continue
            age = (now - when).total_seconds() / 60.0
            kind = classify(False, ahead, line not in head, bool(dirty), inflight, age, a.sla)
            if kind == BRIDGE:
                failures.append("%s: %s — не закоммичено (дерево грязное, впереди origin %d): %s"
                                % (name, BRIDGE, ahead, line[:70]))
            elif kind == UNFINISHED:
                failures.append("%s: %s — впереди origin %d коммит(ов)%s; допушь: %s"
                                % (name, UNFINISHED, ahead,
                                   " (идёт merge/index.lock)" if inflight else "", line[:60]))
            else:
                warnings.append("%s: ещё не в %s (%.0f мин, в пределах SLA): %s"
                                % (name, a.ref, age, line[:60]))

            if CODE.search(line) or PHONE.search(line):
                failures.append("%s: §6 — в несохранённой строке есть код/номер: %s"
                                % (name, line[:80]))

    print("check_owner_chat: файлов %d, строк %d, нет в %s %d | впереди origin %d | in_flight %s"
          % (len(files), total_local, a.ref, total_missing, ahead, inflight))
    for w in warnings:
        print("  ~ %s" % w)
    for f in failures:
        print("  ! %s" % f)
    if failures:
        print("\nИТОГ: %d проблем(ы)" % len(failures))
        return 1
    print("\nИТОГ: строки чата синхронизированы")
    return 0


if __name__ == "__main__":
    sys.exit(main())
