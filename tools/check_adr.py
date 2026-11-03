#!/usr/bin/env python3
"""Гейт формата ADR (PDR-21/D-17): шаблон + протокол согласования.

Зачем: шаблон ADR есть, но «написали по шаблону» проверяется только глазами. Этот гейт
ловит новые ADR без обязательных разделов: варианты (>=3), протокол по ролям, комплаенс.

Проверяются ТОЛЬКО новые ADR (дата >= --since). Старые не трогаем: 24 ADR писались до
шаблона, и красить их — значит сделать гейт вечно красным (дефект гейта, §5.4).

КОПИЯ в проекте: источник правды — LoopMaker/tools/check_adr.py.
"""
import argparse
import datetime
import os
import re
import sys

STATUSES = ("Предложено", "Принято", "Отклонено", "Заменено")
REQUIRED_SECTIONS = ("контекст", "варианты решений", "выбранное решение", "последствия")
DATE_RE = re.compile(r"\*\*Дата:\*\*\s*(\d{4}-\d{2}-\d{2})")
STATUS_RE = re.compile(r"\*\*Статус:\*\*\s*([А-Яа-яA-Za-z]+)")


def check_file(path, strict=False):
    errors, warnings = [], []
    text = open(path, encoding="utf-8-sig").read()
    low = text.lower()

    m = STATUS_RE.search(text)
    if not m:
        errors.append("нет поля «Статус»")
    elif m.group(1) not in STATUSES:
        errors.append("статус «%s» не из списка: %s" % (m.group(1), ", ".join(STATUSES)))

    if not DATE_RE.search(text):
        errors.append("нет поля «Дата: YYYY-MM-DD»")

    for sec in REQUIRED_SECTIONS:
        if sec not in low:
            errors.append("нет раздела «%s»" % sec.capitalize())

    variants = len(re.findall(r"^###\s+вариант\s+[a-zа-я]", low, re.M))
    if variants < 3:
        errors.append("вариантов %d, нужно >= 3 (### Вариант A/B/C)" % variants)

    if "принял" not in low or "отклонил" not in low:
        errors.append("нет протокола по ролям (столбцы «Принял»/«Отклонил»)")
    if "комплаенс" not in low:
        errors.append("нет раздела «Комплаенс» (условия пересмотра, кто проверяет)")

    if strict:
        if "заменяет" not in low:
            warnings.append("нет поля «Заменяет» (для цепочки замен; пустое значение — «—»)")
        if "владелец решения" not in low:
            warnings.append("нет поля «Владелец решения: @роль»")
    return errors, warnings


def iter_adrs(adr_dir):
    for name in sorted(os.listdir(adr_dir)):
        low = name.lower()
        if low.startswith("adr-") and low.endswith(".md") and "template" not in low:
            yield name, os.path.join(adr_dir, name)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Гейт формата ADR")
    ap.add_argument("--dir", default=None, help="папка ADL (по умолчанию 04-architecture/ADL)")
    ap.add_argument("--since", default="2026-11-09", help="проверять ADR с этой даты")
    ap.add_argument("--strict", action="store_true", help="предупреждения считать ошибками")
    ap.add_argument("--selftest", action="store_true", help="негативный контроль (D-12)")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    adr_dir = args.dir or os.path.join("04-architecture", "ADL")
    if not os.path.isdir(adr_dir):
        print("check_adr: нет папки %s" % adr_dir)
        return 2

    since = datetime.date.fromisoformat(args.since)
    checked = skipped = 0
    bad = 0
    for name, path in iter_adrs(adr_dir):
        m = DATE_RE.search(open(path, encoding="utf-8-sig").read())
        if m:
            try:
                if datetime.date.fromisoformat(m.group(1)) < since:
                    skipped += 1
                    continue
            except ValueError:
                pass
        checked += 1
        errors, warnings = check_file(path, args.strict)
        if errors or (args.strict and warnings):
            bad += 1
            print("✗ %s" % name)
            for e in errors:
                print("   ошибка: %s" % e)
            for w in warnings:
                print("   предупреждение: %s" % w)
        elif warnings:
            print("⚠ %s: %s" % (name, "; ".join(warnings)))

    print("check_adr: проверено %d (после %s), старых пропущено %d, нарушений %d"
          % (checked, args.since, skipped, bad))
    return 1 if bad else 0


GOOD = """# ADR-999: тест

- **Статус:** Предложено
- **Дата:** 2026-11-08
- **Заменяет:** —
- **Владелец решения:** @AR

## 1. Контекст
x

## 2. Варианты решений
### Вариант A — a
### Вариант B — b
### Вариант C — c

## 3. Выбранное решение
### 3.1 Протокол согласования
| Роль | Принял | Отклонил | Воздержался |
|---|---|---|---|
| @AR | ✅ | | |

### 3.3 Комплаенс
| Кто проверяет | @AR |

## 4. Последствия
x
"""

BAD = """# ADR-998: тест-плохой

- **Статус:** Придумано
- **Дата:** 2026-11-08

## 1. Контекст
x
"""


def selftest():
    import shutil
    ok = True
    tmp = os.path.join(os.getcwd(), ".tmp-check-adr")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    if True:
        good = os.path.join(tmp, "adr-999-test.md")
        bad = os.path.join(tmp, "adr-998-bad.md")
        open(good, "w", encoding="utf-8", newline="\n").write(GOOD)
        open(bad, "w", encoding="utf-8", newline="\n").write(BAD)

        e_good, w_good = check_file(good, strict=True)
        e_bad, w_bad = check_file(bad, strict=True)
        print("  [%s] хороший ADR -> ошибок %d, предупреждений %d"
              % ("OK " if not e_good and not w_good else "FAIL", len(e_good), len(w_good)))
        ok = ok and not e_good and not w_good
        print("  [%s] плохой ADR -> ошибок %d (ожидали >=5): %s"
              % ("OK " if len(e_bad) >= 5 else "FAIL", len(e_bad), "; ".join(e_bad[:3])))
        ok = ok and len(e_bad) >= 5
        # старый ADR не проверяется
        sys.argv = ["check_adr.py", "--dir", tmp, "--since", "2026-12-01"]
        rc = main()
        print("  [%s] старые пропущены -> rc=%d" % ("OK " if rc == 0 else "FAIL", rc))
        ok = ok and rc == 0
    shutil.rmtree(tmp, ignore_errors=True)
    print("check_adr: самопроверка %s" % ("пройдена" if ok else "ПРОВАЛЕНА"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
