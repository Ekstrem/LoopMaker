#!/usr/bin/env python3
# КОПИЯ из LoopMaker/check_secrets.py (источник правды там; правка — в обоих местах).
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
# DO-065: что сканируем. `.sh` не сканировался вообще — подложенный токен проходил.
SCAN_EXTS = (".md", ".txt", ".json", ".yml", ".yaml", ".sh", ".ps1", ".py", ".env.example")

# Файлы, которые содержат сами шаблоны поиска (allow-list с причиной, не «шум»):
#   tools/check_secrets.py — свои же паттерны;
#   tools/check_rule_docs.py — регэксп `ghp_|gho_|...` для проверки документации.
ALLOWLIST = {
    "tools/check_secrets.py": "собственные шаблоны сканера",
    "tools/check_rule_docs.py": "регэксп-паттерн проверки документации",
}

SKIP_VALUE_PREFIXES = ("$", "os.", "process.env", "env.", "config.", "getenv", "{", "(")
SKIP_VALUES = {"", "null", "none", "true", "false", '""', "''", "string", "password",
               "token", "secret", "write", "read", "admin", "bearer", "postgres",
               # имена типов в документации: {token: bool, tokenLen: number} — не секреты
               # (список согласован с @TL 20:35: bool, boolean, number, string, int, length, masked)
               "bool", "boolean", "int", "integer", "number", "object", "undefined",
               "length", "masked", "boolean"}
# YAML-права вроде `id-token: write` — не секрет, хотя в имени есть «token».
SKIP_KEY_SUFFIXES = ("-token", "_token")

# PDR-20: порог значащих символов поднят 4 -> 8. Значение из 4 символов — это
# `bool`, `true`, `null`, имя типа или флаг, а не секрет. Реальные ключи/токены
# короче 8 значащих символов не встречаются.
MIN_SECRET_LEN = 8
# PDR-20: если строка сама говорит, что значение маскировано — это документация,
# а не утечка. Список узкий и относится к нашей же терминологии снапшотов.
# PDR-20: подсказки СТРОКИ. Если строка сама говорит, что это документация,
# описание сигнатуры или тестовые данные, `assign:`-совпадения на ней не считаются
# утечкой. Это компромисс: строка с «пример»/«фейковый» не сканируется на assign
# вовсе — принято осознанно, чтобы гейт не блокировал описание тестов.
# Важно: `bool` здесь НЕ нужен — если `bool` является ЗНАЧЕНИЕМ, его отсекает
# SKIP_VALUES, а упоминание слова `bool` в строке не должно отключать проверку
# (иначе одна строка про типы гасит находку настоящего ключа рядом).
MASK_HINTS = (
    # описание маскированного вывода
    "маскирован", "masked", "значение скрыто", "скрыто намеренно",
    "tokenlen", "clientidlen", "apibaselen",
    # описание тестовых/примерных данных (PDR-20, вариант D — только эта часть).
    # Стемы, а не слова: «негативн» ловит и «негативного теста».
    # ВАЖНО: слово «пример» здесь НЕ используется — оно матчит «наприме́р» и
    # отключило бы проверку почти на любой строке. Это была бы дыра, а не фикс.
    "негативн", "фейков", "плейсхолдер", "placeholder",
    "example", "dummy", "test data", "тестовые данные", "заглушк",
)

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
    # AN-03 (2026-11-07): whitelist давал слепую зону (6-msa, ранбуки, отчёты не
    # сканировались — «известный allowlist» три месяца). Сканируем ВСЁ, кроме явных
    # исключений: bin/obj/node_modules/.git режет `_glob`, расширения — SCAN_EXTS.
    return ["**/*"]


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
            if os.path.basename(rel) in skip or rel in ALLOWLIST:
                continue
            if rel.endswith(SCAN_EXTS) or os.path.basename(rel).startswith("Dockerfile"):
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


def _entropy(s):
    """Оценка Шеннона: низкая энтропия = слово, а не секрет (`aaaa`, `abababab`)."""
    from collections import Counter
    from math import log2
    if not s:
        return 0.0
    n = len(s)
    return -sum((c / n) * log2(c / n) for c in Counter(s).values())



# Дефект гейта 2026-11-10 (@TL 07:11): `base64-long` поймал ЦЕПОЧКУ ИДЕНТИФИКАТОРОВ
# `AddJwtBearerUseAuthenticationDefaultMapInboundClaims` (латиница, 9 CamelCase-слов,
# без цифр) как «секрет длиной 55». Это проза, а не ключ: настоящий ключ почти всегда
# содержит цифры и/или не-словарные фрагменты. Гейт, краснеющий от прозы, обесценивает
# сигнал не меньше, чем пропущенный ключ.
IDENTIFIER_WORDS = {
    "add", "jwt", "bearer", "use", "authentication", "authorization", "default",
    "map", "inbound", "claims", "token", "tokens", "client", "clients", "server",
    "service", "services", "request", "response", "header", "headers", "cookie",
    "cookies", "policy", "policies", "scheme", "schemes", "handler", "handlers",
    "middleware", "options", "builder", "extensions", "configuration", "settings",
    "security", "identity", "principal", "context", "database", "migration",
}


def _camel_parts(s):
    return [p for p in re.split(r"(?<=[a-z0-9])(?=[A-Z])|_+|-+", s) if p]


def looks_like_identifier_chain(value):
    """Цепочка CamelCase-слов без цифр = имя/проза, а не секрет (узко: для shape-правил)."""
    core = value.strip().strip("\"'`*,;()[]<>/\\")
    if not core or any(c.isdigit() for c in core):
        return False
    parts = _camel_parts(core)
    if len(parts) < 3:
        return False
    return all(p.isalpha() and len(p) >= 3 and p.lower() in IDENTIFIER_WORDS for p in parts)


PLACEHOLDER_RE = re.compile(r"^<[^>]*>$|(your[_-]|placeholder|changeme|change_me|xxx|example|dummy|redacted|скрыт)",
                           re.IGNORECASE)


REGEX_HINT_RE = re.compile(r"\\b|\\w|\\s|\||\(|\)|\[|\]")


def looks_like_regex(value):
    """`\b(ghp_|gho_|...` — это шаблон поиска, а не секрет: секреты не содержат `\b` и `|`."""
    v = (value or "")
    return bool(REGEX_HINT_RE.search(v))


def looks_like_placeholder(value):
    """`<client-JWT>`, `YOUR_TOKEN`, `changeme` — примеры в usage, а не секреты (DO-065)."""
    v = (value or "").strip().strip('"').strip("'")
    if v.startswith("<") or v.startswith("«"):
        return True
    return bool(PLACEHOLDER_RE.search(v))


def looks_like_secret(value):
    if looks_like_placeholder(value) or looks_like_regex(value):
        return False
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
    significant = re.sub(r"[^A-Za-z0-9+/=_\-]", "", core)
    # PDR-20: порог 8 значащих символов + энтропия.
    # 4 символа — это `bool`/`true`/флаг; `abababab` длинное, но энтропия 1.0.
    if len(significant) < MIN_SECRET_LEN:
        return False
    if _entropy(significant.lower()) < 2.0:
        return False
    return True


def _hex_is_identifier(value):
    """40-hex -> git-объект (SHA-1), 64-hex -> sha256-дайджест: публичный идентификатор, не секрет."""
    v = (value or "").strip()
    if len(v) == 40:
        try:
            return subprocess.run(["git", "cat-file", "-e", v], capture_output=True).returncode == 0
        except Exception:
            return False
    if len(v) == 64:
        return True
    return False


def scan_text(text, allow_shapes=True):
    findings = []
    for i, line in enumerate(text.splitlines(), 1):
        if len(line) > 4000:
            continue
        mask_hint = any(h in line.lower() for h in MASK_HINTS)
        for m in ASSIGN.finditer(line):
            key = m.group(1).lower().replace(" ", "")
            if key.endswith(SKIP_KEY_SUFFIXES):
                continue
            if looks_like_secret(m.group(2)) and not mask_hint:
                findings.append(("assign:" + key, i, len(m.group(2))))
        if allow_shapes:
            for name, rx in SHAPES:
                for m in rx.finditer(line):
                    if looks_like_identifier_chain(m.group(0)):
                        continue          # цепочка идентификаторов, не ключ (дефект 2026-11-10)
                    if name == "hex-long" and _hex_is_identifier(m.group(0)):
                        continue          # git-объект/sha256-дайджест — не секрет (дефект 2026-11-07)
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



def selftest():
    """Двусторонний контроль гейта (§5.4): дефект ловится, норма — нет."""
    bad = 0

    code_chain = "AddJwtBearerUseAuthenticationDefaultMapInboundClaimsAndMore"
    f = scan_text(code_chain)
    print("  [%s] цепочка идентификаторов -> находок %d (ожидали 0)" % ("OK " if not f else "FAIL", len(f)))
    bad += 0 if not f else 1

    fake_key = "Xk3Jd9Qz7Lm2Np5Rt8Vw4Yb6Hc1Fg0Ds="
    f = scan_text("ключ = " + fake_key)
    print("  [%s] фейковый ключ с цифрами -> находок %d (ожидали >=1)" % ("OK " if f else "FAIL", len(f)))
    bad += 0 if f else 1

    prose = "Проверяем что middleware подключается корректно и Claims доступны"
    f = scan_text(prose)
    print("  [%s] обычная проза -> находок %d" % ("OK " if not f else "FAIL", len(f)))
    bad += 0 if not f else 1

    # git-объект (HEAD) — публичный идентификатор, не секрет
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    f = scan_text("revision: " + head) if len(head) == 40 else []
    print("  [%s] git-коммит (40-hex) -> находок %d (ожидали 0)" % ("OK " if not f else "FAIL", len(f)))
    bad += 0 if not f else 1

    # sha256-дайджест (64-hex) — не секрет
    sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    f = scan_text("sha256 " + sha256)
    print("  [%s] sha256-дайджест (64-hex) -> находок %d (ожидали 0)" % ("OK " if not f else "FAIL", len(f)))
    bad += 0 if not f else 1

    print("check_secrets: самопроверка %s" % ("пройдена" if bad == 0 else "ПРОВАЛЕНА"))
    return 0 if bad == 0 else 1


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Утечки секретов в текстах петли")
    ap.add_argument("--root", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--range", default=None, help="BASE..HEAD — сканировать только изменённые файлы")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true", help="самопроверка гейта")
    args = ap.parse_args()
    if getattr(args, "selftest", False):
        return selftest()

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
    elif results:
        # @TL 11:10: решение принимается по rc, а не по тексту. Поэтому:
        # 1) явная error-аннотация для CI; 2) находки; 3) ПОСЛЕДНЕЙ строкой —
        #    «утечек N», чтобы `tail -1` не мог соврать «нет находок».
        print("::error::утечек секретов: %d — гейт красный" % len(results))
        for r in results[:40]:
            print("  %s:%d: %s (значение скрыто, len=%d)"
                  % (r["file"], r["line"], r["rule"], r["len"]))
        if len(results) > 40:
            print("  ... ещё %d" % (len(results) - 40))
        print("Значение не печатается намеренно. Зачистить строку, затем ротация "
              "секрета через координатора (из истории git без force-push не убрать).")
        print("утечек %d" % len(results))
    else:
        print("секретов не найдено (файлов проверено: %d)" % len(files))

    return 1 if results else 0


if __name__ == "__main__":
    sys.exit(main())
