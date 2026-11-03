#!/usr/bin/env python3
# КОПИЯ из LoopMaker/check_paths.py (источник правды там; правка — в обоих местах).
"""Guard: роль пишет только в свои папки (по loop.config.json).

Роль определяется по префиксу коммита:
    be:   fe:   do:   te:   ar:   tl:   po:
    chat(te):   docs(do):   fix(9-tests):   feat(be):   ...

Коммиты без узнаваемого префикса (руками, из IDE, «README: ...») не проверяются —
считаются человеческими. Забор ловит агентские коммиты, ваши не блокирует.

    python tools/check_paths.py                      # последний коммит
    python tools/check_paths.py --range HEAD~20..HEAD
    python tools/check_paths.py --range A..B --report-only
    python tools/check_paths.py --zones              # карта зон
    python tools/check_paths.py --zones --strict     # только ядро

Выход 1 — найдены вылеты за зону (в CI это красный job).
"""
import argparse
import json
import os
import re
import subprocess
import sys

CONFIG = "loop.config.json"

# feat(be): ...   chat(te): ...   fix(9-tests): ...   be:
CONV = re.compile(r"^([a-zA-Z][\w-]*)(?:\(([^)]+)\))?!?:\s")


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
        sys.exit("guard: нет %s — это карта зон ролей (см. AGENTS.md §8)" % CONFIG)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def title(cfg, role):
    return cfg.get("role_titles", {}).get(role, role.upper())


def zones_for(cfg, role, strict=False):
    shared = cfg.get("shared", [])
    if role == cfg.get("coordinator", "tl"):
        return list(cfg.get("coordinator_zone", [])) + shared
    zone = (cfg.get("zones") or {}).get(role)
    if zone is None:
        return None
    allowed = list(zone.get("core", []))
    if not strict:
        allowed += zone.get("service", [])
    return allowed + shared


def detect_role(cfg, subject):
    aliases = cfg.get("aliases", {})
    m = CONV.match(subject)
    if m:
        typ, scope = m.group(1).lower(), (m.group(2) or "").lower()
        if scope and scope in aliases:
            return aliases[scope]
        return aliases.get(typ)
    return None


def matches(path, patterns):
    """`dir/*` — поддерево, иначе точное имя файла. AGENTS.md разрешён везде."""
    if path.rsplit("/", 1)[-1] == "AGENTS.md":
        return True
    for p in patterns:
        if p.endswith("/*"):
            base = p[:-2]
            if path == base or path.startswith(base + "/"):
                return True
        elif path == p:
            return True
    return False


def git(*args):
    out = subprocess.run(["git", *args], capture_output=True)
    if out.returncode != 0:
        sys.exit("git %s: %s" % (" ".join(args), out.stderr.decode("utf-8", "ignore").strip()))
    return out.stdout.decode("utf-8", "ignore")


def read_commits(rng):
    raw = git("log", "--no-renames", "--name-only", "--format=%x00%H%x1f%s", rng)
    commits, cur = [], None
    for line in raw.split("\n"):
        if line.startswith("\x00"):
            sha, _, subject = line[1:].partition("\x1f")
            cur = (sha[:7], subject, [])
            commits.append(cur)
        elif line.strip() and cur is not None:
            cur[2].append(line.strip().replace("\\", "/"))
    return commits



DIGEST_PATH = "5-tech-backlog/chat/_dialogue.md"
DIGEST_PREFIX = "chore(chat)"


def digest_violations(repo, rng):
    """Коммиты, которые меняют `_dialogue.md` без признака генерации.

    Правило (@TL 2026-11-11, три случая за сутки): автогенерируемую сводку меняет
    только генератор, и его коммит начинается с `chore(chat)`. Ручной коммит,
    задевший файл, — это либо удалённые строки, либо чужая атрибуция.

    Возвращает [(sha, subject)].
    """
    out = subprocess.run(["git", "log", "--format=%H%x09%s", "--name-only", rng],
                         cwd=repo, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if out.returncode != 0:
        return []
    bad, cur, files = [], None, []
    for line in out.stdout.splitlines():
        if "\t" in line and len(line.split("\t")[0]) >= 7 and all(
                c in "0123456789abcdef" for c in line.split("\t")[0]):
            if cur and DIGEST_PATH in files and not cur[1].startswith(DIGEST_PREFIX):
                bad.append(cur)
            sha, subject = line.split("\t", 1)
            cur, files = (sha, subject), []
        elif line.strip():
            files.append(line.strip())
    if cur and DIGEST_PATH in files and not cur[1].startswith(DIGEST_PREFIX):
        bad.append(cur)
    return bad


def digest_selftest():
    """Проверка правила без git: синтетические пары (subject, files)."""
    cases = [
        (("abc1234", "chore(chat): regenerate _dialogue.md [skip ci]"), [DIGEST_PATH], False),
        (("abc1234", "do: my chat message"), [DIGEST_PATH], True),
        (("abc1234", "te: tests only"), ["9-tests/x.cs"], False),
    ]
    ok = 0
    for (sha, subject), files, expect in cases:
        got = DIGEST_PATH in files and not subject.startswith(DIGEST_PREFIX)
        if got == expect:
            ok += 1
        else:
            print("  [FAIL] %s -> ожидалось %s, получено %s" % (subject[:40], expect, got))
    print("check_paths --selftest: %d/%d" % (ok, len(cases)))
    return 0 if ok == len(cases) else 1

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--range", default="HEAD~1..HEAD")
    ap.add_argument("--report-only", action="store_true", help="не падать, только отчёт")
    ap.add_argument("--zones", action="store_true", help="показать карту зон и выйти")
    ap.add_argument("--strict", action="store_true",
                    help="только ядро — без папок задач и чата")
    ap.add_argument("--config", default=None)
    ap.add_argument("--selftest", action="store_true", help="самопроверка правила сводки")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cfg = load_config(a.config)

    if a.zones:
        print("режим: %s\n" % ("СТРОГО: только ядро" if a.strict else "ядро + служебное"))
        for role in (cfg.get("zones") or {}):
            print("%-12s %s" % (title(cfg, role), ", ".join(zones_for(cfg, role, a.strict))))
        print("%-12s %s" % (title(cfg, cfg.get("coordinator", "tl")),
                            ", ".join(zones_for(cfg, cfg.get("coordinator", "tl")))))
        print("%-12s %s" % ("все", ", ".join(cfg.get("shared", []))))
        return 0

    if a.selftest:
        return digest_selftest()

    commits = read_commits(a.range)
    if not commits:
        print("guard: в диапазоне %s нет коммитов" % a.range)
        return 0

    # правило @TL 2026-11-11: `_dialogue.md` меняет только генератор (chore(chat))
    digest_bad = digest_violations(repo_root(), a.range)
    if digest_bad:
        for sha, subject in digest_bad:
            print("::error::%s меняет %s без признака генерации: «%s» — коммить "
                  "`python tools/merge_chat.py` отдельно с префиксом `chore(chat)`"
                  % (sha[:8], DIGEST_PATH, subject[:60]))
        if not a.report_only:
            return 1

    bad = skipped = checked = 0
    lines = []
    for sha, subject, paths in commits:
        role = detect_role(cfg, subject)
        if not role:
            skipped += 1
            continue
        allowed = zones_for(cfg, role, a.strict)
        if allowed is None:
            skipped += 1
            continue
        checked += 1
        outside = [p for p in paths if not matches(p, allowed)]
        if outside:
            bad += 1
            lines.append("")
            lines.append("✗ %s  [%s]  %s" % (sha, title(cfg, role), subject))
            for p in outside:
                lines.append("    вне зоны: %s" % p)

    if lines and not a.quiet:
        print("\n".join(lines))
        print("")
    print("guard: проверено коммитов %d, пропущено без роли %d, нарушений %d"
          % (checked, skipped, bad))

    if bad and not a.report_only:
        print("\nРоль вышла за свою папку. Что делать:")
        print(" 1) перенести правку в зону владельца (или оформить задачу через chat);")
        print(" 2) если выход согласован — коммитит тот, кому принадлежит зона,")
        print("    с префиксом своей роли;")
        print(" 3) карта зон: python tools/check_paths.py --zones")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
