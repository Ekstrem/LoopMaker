#!/usr/bin/env python3
"""Линтер PDR: решение о процессе — файл по шаблону v2 + актуальный индекс.

Обязательный минимум (шаблон `templates/pdr.md`): варианты (>=2), владелец, согласования,
критерий/метрика; **v2 добавляет** протокол по ролям, обязательства с ответственным и комплаенс.

Правило переходного периода: файлы **старше --since** проверяются по старому минимуму,
файлы от --since — по новому (иначе 20 старых PDR сразу станут «правильно красными» —
тот же класс, что ADR-гейт и фолбэк по дате в docs-budget #49).

КОПИЯ в проекте: источник правды — LoopMaker/tools/check_pdr.py.
"""
import argparse
import datetime
import glob
import os
import re
import sys

REQUIRED = ["Статус", "Владелец", "Согласования"]
OPTIONS_HEAD = "## Варианты"
CRITERIA = ("## Критерий", "## Критерий приёмки", "## Метрика")
STATUSES = ("принято", "обсуждается", "отклонено", "заменено")
# v2: что добавлено к обязательному минимуму
V2_SECTIONS = (
    ("## Решение", "раздел «Решение» (или «Рекомендация»)"),
    ("Протокол", "протокол по ролям (принял/возразил/воздержался)"),
    ("## Обязательства", "раздел «Обязательства» (что / ответственный / как проверяется)"),
    ("## Комплаенс", "раздел «Комплаенс» (источник правды, пересмотр, кто проверяет)"),
)
DATE_RE = re.compile(r"\*\*Дата\*\*\s*\|\s*(\d{4}-\d{2}-\d{2})")


def file_date(body):
    m = DATE_RE.search(body)
    if not m:
        return None
    try:
        return datetime.date.fromisoformat(m.group(1))
    except ValueError:
        return None


def check(path, name, index, since):
    problems = []
    body = open(path, encoding="utf-8-sig").read()
    for field in REQUIRED:
        if "| **%s** |" % field not in body:
            problems.append("%s: нет поля «%s»" % (name, field))
    m = re.search(r"\*\*Статус\*\*\s*\|\s*([^|]+)", body)
    if not m:
        problems.append("%s: статус не читается" % name)
    elif not any(s in m.group(1).strip() for s in STATUSES):
        problems.append("%s: неизвестный статус «%s»" % (name, m.group(1).strip()))
    if OPTIONS_HEAD not in body:
        problems.append("%s: нет раздела «Варианты»" % name)
    if not any(c in body for c in CRITERIA):
        problems.append("%s: нет раздела «Критерий»/«Метрика»" % name)
    if name not in index:
        problems.append("%s: нет строки в индексе docs/pdr/README.md" % name)

    d = file_date(body)
    is_new = d is not None and d >= since
    if is_new:
        for marker, human in V2_SECTIONS:
            markers = (marker,) if isinstance(marker, str) else marker
            if not any(m in body for m in markers):
                problems.append("%s: v2 — нет %s" % (name, human))
    return problems, is_new


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Формат PDR (v2) и актуальность индекса")
    ap.add_argument("--root", default=None)
    ap.add_argument("--since", default="2026-11-09",
                    help="с этой даты действует шаблон v2 (старые — старый минимум)")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    root = args.root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pdr_dir = os.path.join(root, "docs", "pdr")
    if not os.path.isdir(pdr_dir):
        print("check_pdr: нет папки docs/pdr")
        return 1
    since = datetime.date.fromisoformat(args.since)

    index_path = os.path.join(pdr_dir, "README.md")
    index = open(index_path, encoding="utf-8-sig").read() if os.path.exists(index_path) else ""

    problems, new_count, old_count = [], 0, 0
    files = sorted(glob.glob(os.path.join(pdr_dir, "PDR-*.md")))
    for path in files:
        name = os.path.basename(path)
        found, is_new = check(path, name, index, since)
        problems.extend(found)
        new_count += 1 if is_new else 0
        old_count += 0 if is_new else 1

    for link in sorted(set(re.findall(r"\((PDR-\d+\.md)\)", index))):
        if not os.path.exists(os.path.join(pdr_dir, link)):
            problems.append("индекс ссылается на отсутствующий %s" % link)

    print("check_pdr: файлов %d (v2-правила: %d, старый минимум: %d), нарушений %d"
          % (len(files), new_count, old_count, len(problems)))
    for item in problems[:30]:
        print("  ", item)
    if problems and not args.report:
        print("")
        print("Формат: docs/pdr/TEMPLATE.md (канон LoopMaker/templates/pdr.md) — варианты, протокол, "
              "обязательства, комплаенс.")
        return 1
    print("check_pdr: формат и индекс в порядке")
    return 0


if __name__ == "__main__":
    sys.exit(main())
