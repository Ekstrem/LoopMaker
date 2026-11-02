#!/usr/bin/env python3
"""Guard: утечки секретов в текстах петли (чат, задачи, доки).

Зачем отдельно от «правил в AGENTS.md»: правило не сработало. В XDiary роль
BackEnd вписала в outbox **фрагменты** `Jwt:SigningKey` (хвост вида …XXXX=),
чтобы сравнить ключи. Репозиторий публичный, чат коммитится → фрагменты ушли
в историю (коммит 2aa7bf9). Регекс «ключ = значение длиной 20+» такие фрагменты
не видит вообще.

Отсюда два класса правил:

  * `assign` — по **намерению**, а не по форме значения: имя ключа + `=`/`:` +
    хоть что-то. Фрагмент `SigningKey …XXXX=` ловится, потому что значение
    не пустое и не заглушка. Именно этого не хватало в первой версии.
  * `shape` — по форме: длинный base64/hex, JWT (`eyJ…`), `ghp_`/`github_pat_`,
    `AKIA…`, токен Telegram-бота, PEM-заголовок приватного ключа.

Значение НИКОГДА не печатается — только правило, файл, строка и длина.

Использование:
    python tools/check_secrets.py                    # обход по путям из конфига
    python tools/check_secrets.py --range BASE..HEAD # только изменённые файлы (для CI)
    python tools/check_secrets.py --json             # машинный вывод
    python tools/check_secrets.py --root <dir>       # другой проект

Код возврата: 0 — чисто, 1 — найдено.
"""
import argparse
import json
import os
import re
import subprocess
import sys

CONFIG = "loop.config.json"

# Значения, которые не являются секретом: заглушки, ссылки на переменные, литералы.
PLACEHOLDER_HINTS = (
    "changeme", "your-", "yourkey", "your_key", "<", ">", "${", "{{", "***",
    "xxx", "placeholder", "example", "dummy", "redacted", "hidden", "secret-here",
)
SKIP_VALUE_PREFIXES = ("$", "os.", "process.env", "env.", "config.", "getenv", "{", "(")
SKIP_VALUES = {"", "null", "none", "true", "false", '""', "''", "string", "password",
               "token", "secret", "write", "read", "admin", "bearer",
               # имена типов в документации: {token: bool, tokenLen: number} — не секреты
               "bool", "boolean", "int", "integer", "number", "object", "undefined"}
# YAML-права вроде `id-token: write` — не секрет, хотя в имени есть «token».
SKIP_KEY_SUFFIXES = ("-token", "_token")

# Имена, рядом с которыми значение считается секретом.
KEY_NAME = (
    r"(?:jwt[:_\-\s]?signing[_\-\s]?key|signing[_\-\s]?key|api[_\-\s]?key|apikey|"
    r"client[_\-\s]?secret|secret[_\-\s]?key|access[_\-\s]?token|refresh[_\-\s]?token|"
    r"private[_\-\s]?key|project[_\-\s]?pat|garmin[_\-\s]?api[_\-\s]?key|"
    r"password|passwd|pwd|token|secret|bot[_\-\s]?token)"
)
ASSIGN = re.compile(
    r"(?i)\b(" + KEY_NAME + r")\b\s*(?:[:=]|:=|\bis\b)\s*[\"']?"
    r"([^\s\"',;]{1,200})"
)
SHAPES = [
    ("base64-long", re.compile(r"[A-Za-z0-9+/]{32,}={1,2}")),
    ("hex-long", re.compile(r"\b[0-9a-fA-F]{32,}\b")),
    # Хвост base64-ключа, обрезанный для «сравнения»: «…XXXX=», «Xk3Jd9…XXXX=»
    ("fragment-ellipsis", re.compile(r"\.{2,}\s*[A-Za-z0-9+/]{3,}\s*=|[A-Za-z0-9+/]{6,}\.{2,}[A-Za-z0-9+/]{3,}\s*=")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("telegram-bot", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{30,}")),
    ("pem-private", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


def repo_root(arg_root=None):
    if arg_root:
        return os.path.abspath(arg_root)
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True)
        if out.returncode == 0:
            return out.stdout.decode("utf-8", "ignore").strip()
    except Exception:
        pass
    return os.getcwd()


def load_config(root, path=None):
    path = path or os.path.join(root, CONFIG)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def default_paths(cfg):
    backlog = cfg.get("backlog") or "5-backlog"
    chat = cfg.get("chat") or os.path.join(backlog, "chat")
    return [
        os.path.join(chat, "*.md"),
        os.path.join(backlog, "**", "*.md"),
        "README.md", "CHANGELOG.md", "AGENTS.md",
    ]


def expand_patterns(root, cfg):
    """Пути из конфига (или дефолт) -> список относительных файлов."""
    sec = cfg.get("secrets") or {}
    patterns = sec.get("paths") or default_paths(cfg)
    skip = set(sec.get("skip") or ["_dialogue.md", "_lint.md"])
    files = []
    for pat in patterns:
        pat = pat.replace("\\", "/")
        for path in _glob(os.path.join(root, *pat.split("/"))):
            rel = os.path.relpath(path, root).replace("\\", "/")
            if os.path.basename(rel) in skip:
                continue
            if rel.endswith(".md") or rel.endswith(".txt") or rel.endswith(".json") or rel.endswith(".yml"):
                files.append(path)
    return sorted(set(files))


def _glob(pattern):
    import fnmatch
    # «**» в шаблоне -> обход дерева
    if "**" in pattern:
        base = pattern.split("**")[0].rstrip("/\\")
        tail = pattern.split("**", 1)[1].lstrip("/\\")
        root_dir = base if os.path.isdir(base) else os.path.dirname(base)
        out = []
        for dirpath, dirnames, filenames in os.walk(root_dir):
            dirnames[:] = [d for d in dirnames
                           if d not in (".git", "node_modules", "bin", "obj", ".loopmaker")]
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                if fnmatch.fnmatch(full.replace("\\", "/"), pattern.replace("\\", "/")):
                    out.append(full)
        return out
    import glob as g
    return g.glob(pattern, recursive=False)


def looks_like_secret(value):
    if "`" in value:                      # `NAME=` внутри code-span — это имя, не значение
        return False
    v = value.strip().strip("\"'`*,;()[]<>/\\")
    # обрезаем «…2026=» -> «2026=», «Xk3Jd9…2026=» и т.п.
    core = v.lstrip(".").strip().strip("\"'`*,;()[]<>/\\")
    if not core:
        return False
    # «password=`/`passwd=» и прочие перечни имён: знак = допустим только в конце
    if "=" in core[:-1]:
        return False
    low = core.lower()
    if low in SKIP_VALUES:
        return False
    if any(h in low for h in PLACEHOLDER_HINTS):
        return False
    if core.startswith(SKIP_VALUE_PREFIXES):
        return False
    # документация: «GCDC_...», «sk-...» — многоточие в конце = заглушка
    if core.endswith(("...", "…")):
        return False
    # после отрезания многоточий должно остаться >= 4 значащих символов
    return len(re.sub(r"[^A-Za-z0-9+/=_\-]", "", core)) >= 4


def scan_text(text, allow_shapes=True):
    findings = []
    for i, line in enumerate(text.splitlines(), 1):
        if len(line) > 4000:
            continue
        for m in ASSIGN.finditer(line):
            key = m.group(1).lower().replace(" ", "")
            if key.endswith(SKIP_KEY_SUFFIXES):
                continue
            if looks_like_secret(m.group(2)):
                findings.append(("assign:" + key, i, len(m.group(2))))
        if allow_shapes:
            for name, rx in SHAPES:
                for m in rx.finditer(line):
                    if looks_like_secret(m.group(0)):
                        findings.append((name, i, len(m.group(0))))
    return findings


def changed_files(root, rev_range):
    try:
        out = subprocess.run(["git", "-C", root, "diff", "--name-only", *rev_range.split("..")],
                             capture_output=True, text=True, check=False)
        names = [n.strip() for n in out.stdout.splitlines() if n.strip()]
    except OSError:
        return []
    return [os.path.join(root, n) for n in names if os.path.exists(os.path.join(root, n))]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Утечки секретов в текстах петли")
    ap.add_argument("--root", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--range", default=None, help="BASE..HEAD — сканировать только изменённые файлы")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = repo_root(args.root)
    cfg = load_config(root, args.config)
    # Пути из конфига — и в режиме --range тоже: сканируем не «любой изменённый файл»,
    # а только чат/задачи/доки. Иначе в код-файлах ловятся легальные `token = ...`.
    allowed = {os.path.abspath(p) for p in expand_patterns(root, cfg)}
    if args.range:
        files = [f for f in changed_files(root, args.range) if os.path.abspath(f) in allowed]
    else:
        files = sorted(allowed)

    results = []
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        rel = os.path.relpath(path, root).replace("\\", "/")
        for rule, line, length in scan_text(text):
            results.append({"file": rel, "line": line, "rule": rule, "len": length})

    if args.json:
        print(json.dumps({"total": len(results), "findings": results}, ensure_ascii=False, indent=2))
    else:
        if results:
            print("утечек секретов: %d" % len(results), file=sys.stderr)
            for r in results[:40]:
                print("  %s:%d: %s (значение скрыто, len=%d)"
                      % (r["file"], r["line"], r["rule"], r["len"]), file=sys.stderr)
            if len(results) > 40:
                print("  ... ещё %d" % (len(results) - 40), file=sys.stderr)
            print("\nЗначение не печатается намеренно. Зачистить строку, затем ротация "
                  "секрета через координатора (из истории git без force-push не убрать).",
                  file=sys.stderr)
        else:
            print("секретов не найдено (файлов проверено: %d)" % len(files))

    return 1 if results else 0


if __name__ == "__main__":
    sys.exit(main())
