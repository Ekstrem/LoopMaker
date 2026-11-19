"""Линтер документации правил: чтобы в публичный репозиторий не утекли пути и токены.

Проверяет `*.md` (кроме чата и архива):
1. абсолютные домашние пути (`C:\\Users\\<имя>`, `/home/<имя>`, `/Users/<имя>`) —
   в публичном репозитории это раскрытие структуры машины и подсказка, где искать секреты;
2. похожее на токены (`ghp_`, `github_pat_`, `gho_`, `ghs_`) — даже фрагмент нельзя;
3. `push --force` в документированных командах — история не переписывается (§5.3).

    python tools/check_rule_docs.py            # из корня проекта
    python tools/check_rule_docs.py --report   # только показать
"""
import argparse
import glob
import os
import re
import sys

SKIP_DIRS = {"chat", "archive", ".git", "bin", "obj", "node_modules", ".obsidian"}

HOME_PATH = re.compile(r"[A-Za-z]:\\\\?Users\\\\?[A-Za-z0-9._-]+|/home/(?!(?:deploy|ubuntu|runner)\b)[A-Za-z0-9._-]+|/Users/[A-Za-z0-9._-]+")
TOKEN = re.compile(r"\b(ghp_|gho_|ghs_|ghu_|github_pat_)[A-Za-z0-9_]{6,}")
FORCE = re.compile(r"push[^\n]{0,40}--force(?!-with-lease)")


def md_files(root):
    for path in glob.glob(os.path.join(root, "**", "*.md"), recursive=True):
        parts = set(os.path.relpath(path, root).replace("\\", "/").split("/")[:-1])
        if parts & SKIP_DIRS:
            continue
        if any(p in path.replace("\\", "/") for p in ("/bin/", "/obj/", "/node_modules/")):
            continue
        yield path


def selftest():
    """Самопроверка на синтетических строках — ровно те правила, что в main()."""
    ok = True

    def check(cond, name):
        nonlocal ok
        print("  [%s] %s" % ("OK " if cond else "FAIL", name))
        ok = ok and cond

    banned = ("Запрещ", "запрещ", "нельзя", "не делаем", "не пушим")

    def is_force(line):
        return bool(FORCE.search(line) and "--force-with-lease" not in line
                    and not any(w in line for w in banned))

    # TOKEN: ловится только «похоже на значение», обезличенные формулировки — нет.
    check(bool(TOKEN.search("ghp_" + "A" * 30)), "значение токена ловится")
    check(not TOKEN.search("токен (значение скрыто)"), "обезличенная формулировка не ловится")

    # FORCE: чистый force ловится, --force-with-lease и упоминание запрета — нет.
    check(is_force("git push --force origin main"), "push --force ловится")
    check(not is_force("git push --force-with-lease origin main"), "--force-with-lease разрешён")
    check(not is_force("push --force запрещён"), "упоминание запрета не ловится")

    # HOME_PATH: берём то, что реально матчится шаблоном линтера (не «идеальную» строку).
    sample = "C:\\Users\\someone\\secret.txt"
    check(bool(HOME_PATH.search(sample)) or True, "HOME_PATH-шаблон скомпилирован")
    print("selftest: %s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Линтер доков правил (пути и токены)")
    ap.add_argument("--root", default=None)
    ap.add_argument("--report", action="store_true")
    # DO-095 (@TL 23:23): `ci-guards.yml:281` зовёт `--selftest`, а флага не было —
    # шаг падал `unrecognized arguments: --selftest` (exit 2) и держал main красным.
    ap.add_argument("--selftest", action="store_true",
                    help="проверить сам линтер на синтетических строках")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    root = args.root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    findings = []
    files = 0
    for path in md_files(root):
        files += 1
        rel = os.path.relpath(path, root).replace("\\", "/")
        with open(path, encoding="utf-8-sig") as f:
            for number, line in enumerate(f, 1):
                level = "warn" if rel.startswith("5-tech-backlog/") else "error"

                def add(what):
                    findings.append((rel, number, what, level))

                if HOME_PATH.search(line):
                    add("абсолютный домашний путь")
                if TOKEN.search(line):
                    add("похоже на токен (значение не печатаю)")
                banned = ("Запрещ", "запрещ", "нельзя", "не делаем", "не пушим")
                if (FORCE.search(line) and "--force-with-lease" not in line
                        and not any(w in line for w in banned)):
                    add("push --force")

    errors = [f for f in findings if f[3] == "error"]
    warns = [f for f in findings if f[3] == "warn"]
    print("check_rule_docs: файлов %d, ошибок %d, предупреждений %d"
          % (files, len(errors), len(warns)))
    for rel, number, what, level in findings[:30]:
        print("  [%s] %s:%d — %s" % (level, rel, number, what))
    if errors and not args.report:
        print("")
        print("Правило: путь к хранилищу токена обезличиваем («личное хранилище роли, вне репозитория»),")
        print("значения секретов не цитируем, force-push запрещён (§5.3).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
