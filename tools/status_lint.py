#!/usr/bin/env python3
"""Линтер статусов задачи: значение из списка + обязательные поля под статус.

Зачем: пока статусы — «правило на честном слове», в файлах живут синонимы и пустые
поля. В XDiary это уже стоило двух недель расхождения статистики («Выполнена» против
«Закрыта») и одной задачи, закрытой без результата.

Что проверяет:
  1. статус входит в список из `loop.config.json` (`statuses`);
  2. синонимы ловятся отдельно и с подсказкой (`Открыта → ToDo`, `Готово → Закрыта`, …);
  3. обязательные поля под статус (`status_rules` в конфиге, дефолты — ниже):
     `На проработке` → `Прорабатывает`, `В работе` → `В работе с`,
     `В паузе` → `Пауза` + `Разблокирует`, `На проверке` → `Проверяет`,
     `Закрыта` → `## Результат`, `Отклонена` → `Причина`.

Запуск:
    python tools/status_lint.py --config loop.config.json
    python tools/status_lint.py --report                  # отчёт, код 0
    python tools/status_lint.py --statuses "Идея,ToDo,…"  # предпросмотр нового набора
    python tools/status_lint.py --json

Код возврата: 1 — есть ошибки (в `--report` всегда 0).
"""
import argparse
import json
import os
import re
import sys

CONFIG = "loop.config.json"
TASK_ID = re.compile(r"^(?:[A-Z]{2,3})-\d{2,4}\.md$")
STATUS_RE = re.compile(r"Статус:\*\*\s*(.+)")
STATUS_LINE = re.compile(r"^(\s*-\s*\*\*Статус:\*\*\s*)(.+?)(\s*)$")

# Синонимы: значение -> канонический статус. Ловится отдельной ошибкой с подсказкой.
SYNONYMS = {
    "открыта": "ToDo",
    "выполнена": "Закрыта",
    "выполнено": "Закрыта",
    "готово": "Закрыта",
    "done": "Закрыта",
    "сделана": "Закрыта",
    "пауза": "В паузе",
    "идеи": "Идея",
    "в проработке": "На проработке",
    "прорабатывается": "На проработке",
    "проработана": "На проработке",
    "на ревью": "На проверке",
    "todo": "ToDo",
}

# Обязательные поля под статус (если статус есть в конфиге). Значение — список имён полей.
DEFAULT_RULES = {
    "На проработке": ["Прорабатывает"],
    "ToDo": ["Приоритет"],
    "В работе": ["В работе с"],
    "В паузе": ["Пауза", "Разблокирует"],
    "На проверке": ["Проверяет"],
    "Закрыта": ["Результат"],
    "Отклонена": ["Причина"],
}

# Что считается ошибкой сразу, а что — предупреждением до бэкфилла (см. STATUSES.md §5).
# Ключ — «статус:поле».
ERROR_FIELDS = {
    "Закрыта:Результат",        # закрытие без результата — уже нарушение правила
    "В паузе:Пауза",            # пауза без причины бессмысленна
    "В паузе:Разблокирует",     # и без «кто разблокирует» тоже
}


def repo_root(arg=None):
    if arg:
        return os.path.abspath(arg)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def load_config(root, path=None):
    path = path or os.path.join(root, CONFIG)
    if not os.path.exists(path):
        sys.exit("status_lint: нет %s — это карта проекта (см. AGENTS.md §8)" % CONFIG)
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def field_value(text, name):
    """Значение поля `**Имя:** …` (или `## Имя` + первая строка) либо ''."""
    m = re.search(r"\*\*" + re.escape(name) + r":\*\*\s*(.+)", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"^##\s+" + re.escape(name) + r"\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if m:
        return m.group(1).strip()
    return ""


AUTHOR_BY_FOLDER = {
    "arch": "ar",
    "devops": "do",
    "back-end": "be",
    "front-end": "fe",
    "tester": "te",
}


def self_review(rel, text):
    """§2.1 п.6: запрещено саморевью — `Проверяет:` не может быть ролью автора.

    Автор определяется папкой задачи (в своей папке пишет только её владелец).
    Возвращает (автор, ревьюер) при нарушении, иначе None.
    """
    parts = rel.split("/")
    folder = parts[1] if len(parts) > 1 else ""
    author = AUTHOR_BY_FOLDER.get(folder)
    if not author:
        return None
    value = field_value(text, "Проверяет").strip().lower()
    got = ""
    for token in value.replace(",", " ").split():
        if token.startswith("@"):
            got = token.lstrip("@")
            break
    if got and got == author:
        return (author, got)
    return None


def task_files(root, cfg):
    backlog = cfg.get("backlog") or "5-backlog"
    folders = [f.get("folder") for f in (cfg.get("task_folders") or []) if f.get("folder")]
    if not folders and os.path.isdir(os.path.join(root, backlog)):
        folders = [d for d in sorted(os.listdir(os.path.join(root, backlog)))
                   if os.path.isdir(os.path.join(root, backlog, d))]
    out = []
    for folder in folders:
        base = os.path.join(root, backlog, folder)
        if not os.path.isdir(base):
            continue
        for fn in sorted(os.listdir(base)):
            if TASK_ID.match(fn):
                out.append(os.path.join(base, fn))
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Линтер статусов и обязательных полей")
    ap.add_argument("--root", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--statuses", default=None, help="переопределить список через запятую")
    ap.add_argument("--report", action="store_true", help="только отчёт, код 0")
    ap.add_argument("--strict", action="store_true", help="предупреждения тоже валят (после бэкфилла)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = repo_root(args.root)
    cfg = load_config(root, args.config)
    statuses = [s.strip() for s in args.statuses.split(",")] if args.statuses \
        else (cfg.get("statuses") or [])
    rules = dict(DEFAULT_RULES)
    rules.update(cfg.get("status_rules") or {})

    problems = []
    for path in task_files(root, cfg):
        rel = os.path.relpath(path, root).replace("\\", "/")
        with open(path, encoding="utf-8-sig") as f:
            lines = f.readlines()
        text = "".join(lines)
        m = STATUS_RE.search(text)
        if not m:
            problems.append({"file": rel, "status": "", "level": "error",
                             "problem": "нет поля Статус"})
            continue
        raw = m.group(1).strip()
        value = raw.split("(")[0].strip()
        if value not in statuses:
            hint = SYNONYMS.get(value.lower())
            problems.append({"file": rel, "status": value, "level": "error",
                             "problem": "статус вне списка" + (" → %s" % hint if hint else "")})
            continue
        for field in rules.get(value, []):
            got = field_value(text, field)
            if len(got) < 5:
                level = "error" if ("%s:%s" % (value, field)) in ERROR_FIELDS else "warn"
                problems.append({"file": rel, "status": value, "level": level,
                                 "problem": "нет/пусто поле «%s»" % field})
        bad = self_review(rel, text)
        if bad:
            author, got = bad
            problems.append({"file": rel, "status": value, "level": "warn",
                             "problem": "саморевью: автор @%s и `Проверяет: @%s` (§2.1 п.6)"
                                        % (author.upper(), got.upper())})

    errors = [p for p in problems if p["level"] == "error"]
    warns = [p for p in problems if p["level"] == "warn"]

    if args.json:
        print(json.dumps({"total": len(problems), "errors": len(errors), "warnings": len(warns),
                          "statuses": statuses, "problems": problems},
                         ensure_ascii=False, indent=2))
    else:
        if problems:
            print("нарушений: %d (ошибок %d, предупреждений %d; статусов в конфиге: %d)"
                  % (len(problems), len(errors), len(warns), len(statuses)), file=sys.stderr)
            for p in problems[:60]:
                mark = "!" if p["level"] == "error" else "~"
                print("  %s %s: [%s] %s" % (mark, p["file"], p["status"] or "—", p["problem"]),
                      file=sys.stderr)
            if len(problems) > 60:
                print("  ... ещё %d" % (len(problems) - 60), file=sys.stderr)
            print("\n  ! — ошибка (валит CI), ~ — предупреждение (до бэкфилла полей; "
                  "--strict делает их ошибками)", file=sys.stderr)
        else:
            print("статусы в порядке (файлов: %d, статусов: %d)"
                  % (len(task_files(root, cfg)), len(statuses)))

    if args.report:
        return 0
    if args.strict:
        return 1 if problems else 0
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
