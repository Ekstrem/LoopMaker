"""Единый безопасный путь синхронизации с origin: wip по зоне → fetch → rebase → push.

Транспорт. `origin` здесь ssh (`git@github.com:...`). В песочнице DSH **bundled** ssh
из `C:\\Program Files\\Git\\usr\\bin` падает с `couldn't create signal pipe, Win32 error 5`,
а системный OpenSSH работает. Лечится один раз на машину:

    git config --global core.sshCommand "C:/Windows/System32/OpenSSH/ssh.exe"

Скрипт проверяет это до работы и, если ssh недоступен, подсказывает точную команду
(и HTTPS-фолбэк), а не падает молча.

    python tools/git_sync.py --role lm
    python tools/git_sync.py --role lm --dry-run
    python tools/git_sync.py --role lm --paths 5-tech-backlog/chat/lm.md   # узкий wip
"""
import argparse
import os
import subprocess
import sys


def git(*args, check=True):
    out = subprocess.run(["git", *args], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if check and out.returncode != 0:
        return out
    return out


def say(text=""):
    print(text)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="wip + fetch + rebase + push одной командой")
    ap.add_argument("--role", required=True)
    ap.add_argument("--paths", default=None, help="пути для wip (по умолчанию — зона роли)")
    ap.add_argument("--all", action="store_true", help="wip всего дерева (исключение)")
    ap.add_argument("--message", default=None,
                    help="текст коммита (вместо wip-автосейва)")
    ap.add_argument("-u", "--include-untracked", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--branch", default="main")
    args = ap.parse_args()

    dirty = git("status", "--porcelain").stdout.strip()
    steps = []
    if dirty:
        steps.append("wip.py --role %s%s" % (args.role, " --all" if args.all else ""))
    steps += ["git fetch %s" % args.remote,
              "git rebase --autostash %s/%s" % (args.remote, args.branch),
              "git push %s %s" % (args.remote, args.branch)]

    say("git_sync: шаги")
    for step in steps:
        say("   " + step)

    # preflight: транспорт
    probe = git("ls-remote", "--heads", args.remote, args.branch, check=False)
    if probe.returncode != 0:
        say("")
        say("git_sync: транспорт до %s не работает." % args.remote)
        say((probe.stderr or "").strip()[-400:])
        say("")
        say("Лечение (один раз на машину, чинит ssh для всех ролей):")
        say('   git config --global core.sshCommand "C:/Windows/System32/OpenSSH/ssh.exe"')
        say("Если ssh недоступен в принципе — HTTPS-фолбэк: push в https-URL с токеном")
        say("в переменной окружения (не в argv), подробности — docs/CI-RULES.md, раздел «Пуш».")
        return 2
    say("git_sync: транспорт ok")

    # Висящий rebase/автостэш: `rebase --autostash` в цикле с push оставляет за собой
    # `.git/rebase-merge` без `head-name`, после чего ни один rebase не стартует.
    git_dir = git("rev-parse", "--git-dir").stdout.strip() or ".git"
    for marker in ("rebase-merge", "rebase-apply"):
        path = os.path.join(git_dir, marker)
        if os.path.exists(path):
            say("")
            say("git_sync: найден незавершённый rebase (%s)." % marker)
            say("  Сначала восстанови и объяви в чат (§5.3): `git rebase --abort`;")
            say("  если abort не работает (could not read head-name) — это повреждённые")
            say("  метаданные: объявить `→ всем`, восстанавливает координатор, автостэш")
            say("  спасать через `git stash apply <sha>`, SHA записать в чат.")
            return 2

    if args.dry_run:
        say("dry-run: дальше не иду")
        return 0

    if dirty:
        cmd = [sys.executable, "tools/wip.py", "--role", args.role]
        if args.all:
            cmd.append("--all")
        if args.paths:
            cmd += ["--paths", args.paths]
        if args.include_untracked:
            cmd.append("-u")
        if args.message:
            cmd += ["--message", args.message]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
        say((res.stdout or "").strip())
        if res.returncode != 0:
            sys.exit("git_sync: wip не удался — дерево не тронуто")

    fetched = git("fetch", args.remote, check=False)
    if fetched.returncode != 0:
        sys.exit("git_sync: fetch не прошёл: %s" % (fetched.stderr or "").strip()[-300:])

    rebase = git("rebase", "--autostash", "%s/%s" % (args.remote, args.branch), check=False)
    if rebase.returncode != 0:
        say((rebase.stderr or "").strip()[:600])
        sys.exit("git_sync: rebase не прошёл — разбирай вручную (§5.3: STOP при потере)")
    say("git_sync: rebase ok")

    ahead = git("rev-list", "--count", "%s/%s..HEAD" % (args.remote, args.branch)).stdout.strip()
    if ahead == "0":
        say("git_sync: нечего пушить")
        return 0

    push = git("push", args.remote, args.branch, check=False)
    if push.returncode != 0:
        say((push.stderr or "").strip()[-400:])
        sys.exit("git_sync: push не прошёл — возможно, кто-то успел раньше: повтори команду")
    say("git_sync: запушено (%s коммит(ов))" % ahead)

    check = git("rev-list", "--count", "%s/%s..HEAD" % (args.remote, args.branch)).stdout.strip()
    say("git_sync: проверка после пуша — впереди origin: %s" % check)
    return 0


if __name__ == "__main__":
    sys.exit(main())
