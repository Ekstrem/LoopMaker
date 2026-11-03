# КОПИЯ из LoopMaker/git_sync.py (источник правды там; правка — в обоих местах).
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
import subprocess
import argparse
import time
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


def classify_rebase_files(marker, names):
    """Чистое решение по содержимому каталога rebase (без файловой системы).

    «Пустой» — это `rebase-merge`, в котором НЕТ `head-name`/`onto` и лежит ТОЛЬКО
    `autostash`. Именно так остаётся после `rebase --autostash` на грязном дереве.
    Всё остальное — `other` (живой rebase, руками не трогаем).
    """
    names = sorted(names)
    if marker != "rebase-merge":
        return "other"
    if "head-name" in names or "onto" in names:
        return "other"
    if names == ["autostash"]:
        return "empty-autostash"
    return "other"


def decide_fix_state(in_stash, applied, force):
    """Можно ли снимать ПУСТОЕ rebase-состояние. Чистая функция — её и тестируем.

    in_stash — автостэш есть в `git stash list` (работа сохранена);
    applied  — содержимое автостэша уже в рабочем дереве (`git diff <sha>` пуст);
    force    — пользователь передал --force-drop-autostash.
    Возвращает (разрешено, причина).
    """
    if in_stash:
        return True, "автостэш есть в stash — снимаем пустое состояние, работа сохранена"
    if applied and force:
        return True, "автостэша нет в stash, но его изменения уже в дереве — снимаем по --force-drop-autostash"
    if applied and not force:
        return False, ("автостэша нет в stash, но его изменения уже в дереве: повторный запуск с "
                       "--force-drop-autostash снимет пустое состояние")
    return False, ("автостэша нет в stash и его изменения НЕ в дереве — сначала восстанови: "
                   "git stash apply <sha>")


def rebase_state(git_dir):

    """Разбор состояния rebase БЕЗ изменений: что за маркер и можно ли его безопасно убрать.

    «Пустой» = есть `.git/rebase-merge`, внутри НЕТ `head-name`/`onto` и в каталоге лежит
    ТОЛЬКО `autostash`. Именно такое состояние остаётся после `rebase --autostash` на грязном
    дереве (инцидент 02.11, повторился трижды). Вид:
      {"marker": None|"rebase-merge"|"rebase-apply", "kind": "healthy"|"empty-autostash"|"other",
       "autostash": "<sha>"|None, "in_stash": bool}
    """
    state = {"marker": None, "kind": "healthy", "autostash": None, "in_stash": False}
    for marker in ("rebase-merge", "rebase-apply"):
        path = os.path.join(git_dir, marker)
        if not os.path.isdir(path):
            continue
        state["marker"] = marker
        names = sorted(os.listdir(path))
        state["kind"] = classify_rebase_files(marker, names)
        autostash_file = os.path.join(path, "autostash")
        if os.path.isfile(autostash_file):
            try:
                with open(autostash_file, encoding="utf-8", errors="replace") as fh:
                    state["autostash"] = fh.read().strip()
            except OSError:
                state["autostash"] = None
        if state["autostash"]:
            listing = git("stash", "list", "--format=%H", check=False).stdout
            state["in_stash"] = state["autostash"] in (listing or "")
        return state
    return state


LOCK_NAME = "xdiary-sync.lock"
LOCK_TTL_MIN = 10


def lock_path(git_dir):
    return os.path.join(git_dir, LOCK_NAME)


def lock_is_fresh(git_dir, ttl_min=LOCK_TTL_MIN, now=None):
    """Есть ли СВЕЖИЙ замок другого git_sync. Чистая логика по времени — тестируется."""
    import time as _t
    path = lock_path(git_dir)
    if not os.path.exists(path):
        return False, None
    age_min = ((now or _t.time()) - os.path.getmtime(path)) / 60.0
    return (age_min < ttl_min), age_min


def write_lock(git_dir, role):
    import time as _t
    try:
        with open(lock_path(git_dir), "w", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (role, int(_t.time())))
        return True
    except OSError:
        return False


def drop_lock(git_dir):
    try:
        os.remove(lock_path(git_dir))
    except OSError:
        pass


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="wip + fetch + rebase + push одной командой")
    ap.add_argument("--role", required=True)
    ap.add_argument("--paths", default=None, help="пути для wip (по умолчанию — зона роли)")
    ap.add_argument("--all", action="store_true", help="wip всего дерева (исключение)")
    ap.add_argument("--message", default=None,
                    help="текст коммита (вместо wip-автосейва)")
    ap.add_argument("--hold", action="store_true",
                    help="не пушить main: собрать ветку hold/<role> для MR (окно тишины)")
    ap.add_argument("--no-owner-chat", action="store_true",
                    help="не проверять после пуша, что строки владельца доехали в origin (D-16)")
    ap.add_argument("-u", "--include-untracked", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--ignore-lock", action="store_true",
                    help="не считать свежий замок другого sync причиной отказа")
    ap.add_argument("--zone", action="store_true",
                    help="wip по всей зоне роли (старое поведение). По умолчанию wip идёт "
                         "ТОЛЬКО по явным --paths: зона загребает чужие правки (DO-043)")
    ap.add_argument("--force-drop-autostash", action="store_true",
                    help="с --fix-state: разрешить снятие пустого состояния, если автостэш уже "
                         "применён к дереву (git diff <sha> пуст) и записи в stash нет")
    ap.add_argument("--fix-state", action="store_true",
                    help="убрать ТОЛЬКО пустой rebase-merge (без head-name/onto, один autostash) "
                         "и напечатать, что сделано; SHA автостэша сохраняется")
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
    state = rebase_state(git_dir)

    if state["marker"]:
        if args.fix_state and state["kind"] == "empty-autostash":
            say("")
            say("git_sync --fix-state: найден ПУСТОЙ %s (нет head-name/onto, только autostash)." % state["marker"])
            say("  SHA автостэша: %s (полный — в %s/autostash)" % (
                (state["autostash"] or "")[:8], os.path.join(git_dir, state["marker"])))
            applied = False
            if state["autostash"] and not state["in_stash"]:
                # Corner-case (@TL 10:10): состояние может остаться после того, как автостэш
                # уже применили и запись из stash убрали. Тогда «нет в stash» ещё не значит
                # «работа пропадёт» — проверяем, пуст ли diff между SHA и деревом.
                diff = git("diff", "--quiet", state["autostash"], check=False)
                applied = diff.returncode == 0
            allowed, reason = decide_fix_state(state["in_stash"], applied, args.force_drop_autostash)
            if not allowed:
                say("  ОТКАЗ: %s" % reason)
                return 2
            say("  %s" % reason)
            removed = os.path.join(git_dir, state["marker"])
            try:
                import shutil
                shutil.rmtree(removed)
            except OSError as exc:
                say("  ОТКАЗ: не смог удалить %s: %s" % (removed, exc))
                return 2
            say("  удалено: %s (автостэш сохранён в stash, ничего не потеряно)" % removed)
            say("  теперь повтори обычный запуск: git_sync.py --role <роль>")
            return 0

        say("")
        say("git_sync: найден незавершённый rebase (%s)." % state["marker"])
        if state["kind"] == "empty-autostash":
            say("  Похоже на ПУСТОЙ rebase-merge (только autostash). Разобрать безопасно:")
            say("  `python tools/git_sync.py --role %s --fix-state` — уберёт только пустое" % args.role)
            say("  состояние, SHA автостэша будет напечатан и проверен по stash.")
        say("  Иначе (§5.3): объявить `→ всем`, `git rebase --abort`; если не работает —")
        say("  восстанавливает координатор, автостэш спасать `git stash apply <sha>`.")
        return 2

    if args.dry_run:
        say("dry-run: дальше не иду")
        return 0

    # Инцидент @TE 02.11: коммит, созданный во время ЧУЖОГО rebase, остаётся вне ветки.
    # Второй sync в том же дереве — тот же риск, поэтому берём замок и не запускаемся поверх.
    fresh, age = lock_is_fresh(git_dir)
    if fresh and not args.ignore_lock:
        say("")
        say("git_sync: идёт ЧУЖОЙ sync (замок %.0f мин назад). Второй не запускаю:" % age)
        say("  параллельные rebase в одном дереве теряют коммиты — коммит, созданный во")
        say("  время чужого rebase, остаётся вне ветки. Подожди или, если замок зря: --ignore-lock.")
        return 3

    if write_lock(git_dir, args.role):
        import atexit
        atexit.register(drop_lock, git_dir)

    # Предохранитель (по инциденту 02.11): рапорт «запушено (1 коммит)» при непопавших
    # файлах опаснее ошибки. Если в зоне роли есть untracked, а `-u` не передан — не идём.
    NOISE = (".obsidian/", "log", "rep", "msg", ".tmp-", "node_modules/")
    untracked = git("ls-files", "--others", "--exclude-standard").stdout.splitlines()
    untracked = [u for u in untracked if u.strip() and not any(n in u for n in NOISE)]
    if untracked and not args.include_untracked and not args.all:
        say("")
        say("git_sync: ВНИМАНИЕ — %d untracked-файлов вне коммита:" % len(untracked))
        for name in untracked[:10]:
            say("   ? " + name)
        if len(untracked) > 10:
            say("   … ещё %d" % (len(untracked) - 10))
        say("   они не попадут в пуш. Добавь `-u` (все) или `--paths` (выборочно),")
        say("   либо удали/заигнорь их — иначе получишь рапорт «запушено» без содержимого.")
        return 1

    if dirty and not args.paths and not args.all and not args.zone and not args.include_untracked:
        # DO-043: wip по зоне загребает чужое — так `tools/health.py` (@LM) уехал в коммит @DO.
        # Поэтому по умолчанию требуем явные пути: молчаливая угадайка хуже лишней команды.
        say("")
        say("git_sync: в дереве есть изменения, но не указано, ЧТО коммитить.")
        say("  По умолчанию wip идёт только по явным путям (DO-043), иначе в коммит уедет чужое.")
        say("  Варианты:")
        say("    --paths 7-deploy/runbooks/ tools/test_git_sync_state.py   # выборочно")
        say("    --zone                                                    # вся зона роли (старое)")
        say("    --all                                                     # всё дерево (исключение)")
        say("    -u                                                        # + untracked")
        say("  Изменённые файлы сейчас:")
        for line in git("status", "--porcelain").stdout.splitlines()[:12]:
            say("    " + line.strip()[:120])
        return 1

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

    if args.hold:
        # Окно тишины: main не трогаем, собираем ветку и просим координатора смержить MR.
        name = "hold/%s-%s" % (args.role, time.strftime("%Y%m%d-%H%M"))
        git("branch", "-f", name, "HEAD")
        out = git("push", "-u", args.remote, name, check=False)
        say("")
        say("git_sync --hold: main не тронут, ветка %s отправлена" % name)
        say((out.stderr or out.stdout).strip()[-240:])
        say("")
        say("Дальше: строка → @TL в свой outbox «мерж %s после окна» " % name)
        say("и задача в его папке. Ветка не запускает тяжёлые workflow (они на main).")
        return 0

    push = git("push", args.remote, args.branch, check=False)
    if push.returncode != 0:
        say((push.stderr or "").strip()[-400:])
        sys.exit("git_sync: push не прошёл — возможно, кто-то успел раньше: повтори команду")
    say("git_sync: запушено (%s коммит(ов))" % ahead)

    check = git("rev-list", "--count", "%s/%s..HEAD" % (args.remote, args.branch)).stdout.strip()
    say("git_sync: проверка после пуша — впереди origin: %s" % check)

    # D-16 (LoopMaster 19:32): строки владельца из Telegram обязаны оказаться в origin.
    # В CI эта проверка вырождается (дерево чистое), поэтому точка подключения — ЗДЕСЬ:
    # пушащая роль сразу видит, что её строка не доехала. Проверка не блокирует пуш
    # (он уже прошёл), но завершает команду ненулевым кодом, чтобы это не потерялось.
    if not getattr(args, "no_owner_chat", False):
        owner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_owner_chat.py")
        if os.path.exists(owner):
            r = subprocess.run([sys.executable, owner], capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            out = (r.stdout or r.stderr or "").strip()
            if r.returncode != 0:
                say("git_sync: ВНИМАНИЕ — строка владельца не доехала в origin:")
                for line in out.splitlines()[-8:]:
                    say("  " + line)
                say("git_sync: коммит строки — за раннером/агентом (не за плагином моста)")
                return 1
            say("git_sync: строки владельца синхронизированы (D-16)")

    say("git_sync: если кто-то коммитил, пока шёл этот sync — проверь reflog:"
        " его коммит мог осиротеть; восстановление — git cherry-pick <sha>")
    return 0


if __name__ == "__main__":
    sys.exit(main())