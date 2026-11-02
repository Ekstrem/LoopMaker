"""WIP-коммит перед rebase/pull: общее дерево не должно терять чужую работу.

Зачем: у всех ролей **один** рабочий каталог. Любой `pull --rebase`/`rebase`
может унести незакоммиченную правку соседа (так уже терялись `paths`-правки @DO).
Правило: перед любой операцией, которая трогает дерево, — `python tools/wip.py --role XX`.

    python tools/wip.py --role lm             # закоммитить изменённые файлы (tracked)
    python tools/wip.py --role lm -u          # вместе с новыми файлами
    python tools/wip.py --dry-run             # только показать, что попадёт в коммит
"""
import argparse
import subprocess
import sys
import time


def git(*args, check=True):
    out = subprocess.run(["git", *args], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if check and out.returncode != 0:
        sys.exit("git %s: %s" % (" ".join(args), (out.stderr or "").strip()[:300]))
    return out.stdout.strip()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="WIP-коммит перед операциями с деревом")
    ap.add_argument("--role", required=True, help="префикс роли: be, fe, do, te, ar, tl, po, lm")
    ap.add_argument("-u", "--include-untracked", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--message", default=None)
    args = ap.parse_args()

    staged = git("diff", "--cached", "--name-only")
    if staged:
        print("в индексе уже есть изменения — коммичу их как есть")
    else:
        changed = git("diff", "--name-only")
        untracked = git("ls-files", "--others", "--exclude-standard") if args.include_untracked else ""
        files = [f for f in (changed.splitlines() + untracked.splitlines()) if f.strip()]
        if not files:
            print("wip: нечего коммитить (дерево чистое)")
            return 0
        print("wip: файлов %d" % len(files))
        for name in files[:15]:
            print("  ", name)
        if len(files) > 15:
            print("  … ещё %d" % (len(files) - 15))
        if args.dry_run:
            return 0
        git("add", "--", *files)

    if args.dry_run:
        return 0

    stamp = time.strftime("%Y-%m-%d %H:%M")
    message = args.message or "wip(%s): автосейв перед операцией с деревом %s" % (args.role, stamp)
    git("commit", "-q", "-m", message)
    print("wip: коммит создан —", git("log", "--oneline", "-1"))
    print("Дальше можно безопасно делать pull --rebase / rebase.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
