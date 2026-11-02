"""Какие workflow запустит мой пуш: проверка перед `git push`.

Причина: правки `docs/**`, `tools/**`, `.github/**` **триггерят** `ci-tests`, даже если это
«просто документация» — и вытесняют ожидающий прогон гейта (так потерялись `#977`–`#983`).
Поэтому перед пушем смотрим, что он запустит, и при окне тишины (@TL) в эти пути не пушим.

    python tools/what_triggers.py                 # изменения в рабочем дереве vs origin/main
    python tools/what_triggers.py --range HEAD~1..HEAD
    python tools/what_triggers.py --paths docs/pdr/PDR-19.md tools/foo.py

Код 1 — если запустится хотя бы один workflow (то есть пуш «тяжёлый»), 0 — если тишина.
"""
import argparse
import fnmatch
import os
import re
import subprocess
import sys

WF_DIR = ".github/workflows"
# Упрощённый матчер: GitHub использует minimatch, где `*` не пересекает `/`,
# а `**` пересекает. Для наших фильтров достаточно.
def matches(pattern, path):
    p = path.replace("\\", "/")
    pattern = pattern.strip().strip("'\"")
    if pattern.endswith("/**"):
        return p == pattern[:-3] or p.startswith(pattern[:-2])
    if "**" in pattern:
        rx = re.escape(pattern).replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
        return re.match("^%s$" % rx, p) is not None
    if "/" not in pattern:
        # GitHub: `*` не пересекает `/`, поэтому `*.md` покрывает только файлы в корне.
        # Именно из-за этого `docs/pdr/*.md` до сих пор триггерит `ci-tests`.
        return "/" not in p and fnmatch.fnmatch(p, pattern)
    return fnmatch.fnmatch(p, pattern)


def git(*args):
    out = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8",
                        errors="replace")
    return out.stdout.strip()


def changed_paths(args):
    if args.paths:
        return [p.strip() for p in args.paths]
    if args.range:
        return [p for p in git("diff", "--name-only", args.range).splitlines() if p.strip()]
    if args.staged:
        return [p for p in git("diff", "--cached", "--name-only").splitlines() if p.strip()]
    out = git("diff", "--name-only", "origin/main...HEAD")
    return [p for p in out.splitlines() if p.strip()]


def workflows():
    result = []
    for name in sorted(os.listdir(WF_DIR)):
        if not name.endswith((".yml", ".yaml")):
            continue
        body = open(os.path.join(WF_DIR, name), encoding="utf-8-sig").read()
        push = re.search(r"^on:\s*\n(.*?)(?=^\S|\Z)", body, re.S | re.M)
        block = push.group(0) if push else body[:2000]
        if "push" not in block and not re.search(r"^on:\s*\[?push", body, re.M):
            continue
        includes = re.findall(r"^\s{4,6}paths:\s*\n((?:\s+-.*\n?)+)", body, re.M)
        ignores = re.findall(r"^\s{4,6}paths-ignore:\s*\n((?:\s+-.*\n?)+)", body, re.M)

        def clean(chunks):
            out = []
            for chunk in chunks:
                for line in chunk.splitlines():
                    line = line.strip()
                    if line.startswith("- "):
                        out.append(line[2:].strip().strip("'\""))
            return out

        result.append((name, clean(includes), clean(ignores)))
    return result


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Какие workflow запустит пуш")
    ap.add_argument("--range", default=None)
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--paths", nargs="*", default=None)
    args = ap.parse_args()

    paths = changed_paths(args)
    if not paths:
        print("what_triggers: изменений нет — пуш ничего не запустит")
        return 0

    print("what_triggers: файлов %d" % len(paths))
    fired = []
    for name, includes, ignores in workflows():
        if includes:
            hit = any(any(matches(inc, p) for inc in includes) for p in paths)
        elif ignores:
            hit = any(not any(matches(ign, p) for ign in ignores) for p in paths)
        else:
            hit = True
        if hit:
            fired.append(name)

    if not fired:
        print("  запусков нет ни в одном workflow — пуш «тихий»")
        return 0
    for name in fired:
        print("  запустит:", name)
    print("")
    print("Если действует окно тишины (@TL) — эти правки не пушим: они займут очередь")
    print("и вытеснят ожидающий прогон гейта. Чат/задачи (`5-tech-backlog/**`) — тихие.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
