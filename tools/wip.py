"""WIP-коммит перед операциями с деревом — только по путям роли.

Зачем: у всех ролей **один** рабочий каталог. Любой `pull --rebase`/`rebase` может
унести незакоммиченную правку соседа (так терялись `paths`-правки @DO). При этом
`git add -A` в общем дереве захватывает чужие файлы: был случай `wip(be)` с `AGENTS.md`
внутри — авторство вводит в заблуждение.

Поэтому по умолчанию wip берёт **только зону роли** из `loop.config.json`:

    python tools/wip.py --role be              # только core+service зоны be
    python tools/wip.py --role be --all        # всё дерево (осознанное исключение)
    python tools/wip.py --role be --paths 6-msa,5-tech-backlog/back-end
    python tools/wip.py --role be --dry-run
"""
import argparse
import json
import os
import subprocess
import sys
import time


def git(*args, check=True):
    out = subprocess.run(["git", *args], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if check and out.returncode != 0:
        sys.exit("git %s: %s" % (" ".join(args), (out.stderr or "").strip()[:300]))
    return out.stdout.strip()


def zone_prefixes(root, role):
    """Префиксы зоны роли из loop.config.json: [core..., service...] без '/*'."""
    path = os.path.join(root, "loop.config.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    zone = (cfg.get("zones") or {}).get(role) or {}
    raw = list(zone.get("core") or []) + list(zone.get("service") or [])
    out = []
    for item in raw:
        item = str(item).strip()
        if item.endswith("/*"):
            item = item[:-2]
        out.append(item.rstrip("/"))
    return out


def in_zone(path, prefixes):
    p = path.replace("\\", "/")
    return any(p == pre or p.startswith(pre + "/") for pre in prefixes)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="WIP-коммит по зоне роли")
    ap.add_argument("--role", required=True, help="be, fe, do, te, ar, tl, po, lm")
    ap.add_argument("--paths", default=None, help="свои пути через запятую (перекрывает зону)")
    ap.add_argument("--all", action="store_true", help="всё дерево (исключение, объясни в коммите)")
    ap.add_argument("-u", "--include-untracked", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--message", default=None)
    args = ap.parse_args()

    root = git("rev-parse", "--show-toplevel")
    prefixes = [p.strip() for p in args.paths.split(",")] if args.paths else zone_prefixes(root, args.role)
    staged = git("diff", "--cached", "--name-only")
    if staged:
        print("в индексе уже есть изменения — коммичу их как есть")
        files = staged.splitlines()
    else:
        changed = git("diff", "--name-only").splitlines()
        if args.include_untracked:
            changed += git("ls-files", "--others", "--exclude-standard").splitlines()
        files = [f for f in changed if f.strip()]
        if not args.all and prefixes:
            skipped = [f for f in files if not in_zone(f, prefixes)]
            files = [f for f in files if in_zone(f, prefixes)]
            if skipped:
                print("wip: вне зоны %s — не трогаю (%d):" % (args.role, len(skipped)))
                for name in skipped[:8]:
                    print("   -", name)
                if len(skipped) > 8:
                    print("   … ещё %d" % (len(skipped) - 8))
                print("   если это твоя правка — `--paths` или `--all`")
        if not files:
            print("wip: в зоне %s нечего коммитить" % args.role)
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
    print("Дальше можно безопасно делать синхронизацию: python tools/git_sync.py --role %s" % args.role)
    return 0


if __name__ == "__main__":
    sys.exit(main())
