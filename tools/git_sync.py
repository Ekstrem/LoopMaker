"""Единый безопасный путь синхронизации с origin: wip → fetch → rebase → push.

Зачем: у всех ролей один рабочий каталог. `pull --rebase` без савейва уносит чужую
незакоммиченную правку (так терялись `paths`-правки @DO). Правило — делать это одной
командой, а не «как получится».

    python tools/git_sync.py --role lm            # wip (tracked) + rebase + push
    python tools/git_sync.py --role lm -u         # + новые файлы
    python tools/git_sync.py --role lm --dry-run  # показать шаги, ничего не менять

Хуки git (`pre-rebase`) здесь не годятся: в песочнице DSH запрещён запуск sh,
и любой хук ломал бы rebase целиком.
"""
import argparse
import subprocess
import sys


def git(*args, check=True):
    out = subprocess.run(["git", *args], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if check and out.returncode != 0:
        sys.exit("git %s: %s" % (" ".join(args), (out.stderr or "").strip()[:400]))
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="wip + fetch + rebase + push одной командой")
    ap.add_argument("--role", required=True)
    ap.add_argument("-u", "--include-untracked", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--branch", default="main")
    args = ap.parse_args()

    steps = []

    dirty = git("status", "--porcelain").stdout.strip()
    if dirty:
        steps.append("wip.py --role %s%s" % (args.role, " -u" if args.include_untracked else ""))
    steps += ["git fetch %s" % args.remote,
              "git rebase --autostash %s/%s" % (args.remote, args.branch),
              "git push %s %s" % (args.remote, args.branch)]

    print("git_sync: шаги")
    for step in steps:
        print("  ", step)
    if args.dry_run:
        print("dry-run: ничего не выполнено")
        return 0

    if dirty:
        cmd = [sys.executable, "tools/wip.py", "--role", args.role]
        if args.include_untracked:
            cmd.append("-u")
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
        print((res.stdout or "").strip())
        if res.returncode != 0:
            sys.exit("git_sync: wip не удался — дальше не иду (дерево не тронуто)")

    git("fetch", args.remote)
    rebase = git("rebase", "--autostash", "%s/%s" % (args.remote, args.branch), check=False)
    if rebase.returncode != 0:
        print((rebase.stderr or "").strip()[:600])
        sys.exit("git_sync: rebase не прошёл — разбирайся вручную (см. §5.3: STOP при потере)")
    print("git_sync: rebase ok")

    ahead = git("rev-list", "--count", "%s/%s..HEAD" % (args.remote, args.branch)).stdout.strip()
    if ahead == "0":
        print("git_sync: нечего пушить")
        return 0
    push = git("push", args.remote, args.branch, check=False)
    print((push.stderr or "").strip()[-200:] if push.returncode == 0 else (push.stderr or "").strip()[:400])
    if push.returncode != 0:
        sys.exit("git_sync: push не прошёл — возможно, кто-то успел раньше: повтори команду")
    print("git_sync: запушено")
    return 0


if __name__ == "__main__":
    sys.exit(main())
