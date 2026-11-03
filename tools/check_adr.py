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
import subprocess
import sys

STATUSES = ("Предложено", "Принято", "Отклонено", "Заменено")
REQUIRED_SECTIONS = ("контекст", "варианты решений", "выбранное решение", "последствия")
DATE_RE = re.compile(r"\*\*Дата:\*\*\s*(\d{4}-\d{2}-\d{2})")
STATUS_RE = re.compile(r"\*\*Статус:\*\*\s*([А-Яа-яA-Za-z]+)")


PLUS_RE = re.compile(r"[-|]\s*\**(Плюсы|Минусы)", re.I)


def count_items(text, label):
    """Сколько пунктов в блоке «Плюсы/Минусы» (bullets или через `;`)."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not re.search(label, line, re.I):
            continue
        # табличный вариант: | Плюсы | a; b; c |
        if "|" in line:
            cells = [c.strip() for c in line.split("|") if c.strip()]
            body = cells[-1] if cells else ""
            body = re.sub(r"^\**%s[^:]*:\**" % label, "", body, flags=re.I)
            items = [x for x in re.split(r"[;]", body) if x.strip()]
            if len(items) > 1:
                return len(items)
            return len([x for x in body.split(",") if x.strip()])
        # bullet-вариант: пункты могут быть и в этой же строке через «;», и списком ниже
        inline = re.sub(r"^[-|]\s*\**%s[^:]*:\**" % label, "", line, flags=re.I).strip()
        if inline:
            parts = [x for x in re.split(r"[;]", inline) if x.strip()]
            if len(parts) > 1:
                return len(parts)
            count = 1 if parts else 0
        else:
            count = 0
        for nxt in lines[i + 1:]:
            s = nxt.strip()
            if not s:
                continue
            if s.startswith(("- ", "* ")) and not re.search(r"\*\*(Плюсы|Минусы|Описание|Соответствие)", s):
                count += 1
                continue
            break
        return count
    return 0

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
        # Различаем «поля нет» и «поле есть, но не дата»: иначе роль с плейсхолдером
        # YYYY-MM-DD видит «нет поля», хотя поле есть (нашёл @LM при проверке скелета).
        m_date = re.search(r"\*\*Дата:\*\*\s*([^\n]*)", text)
        if m_date:
            errors.append("поле «Дата» не в формате YYYY-MM-DD (значение: %s) — подставь реальную дату, например 2026-11-09, а не плейсхолдер из шаблона"
                          % m_date.group(1).strip()[:40])
        else:
            errors.append("нет поля «Дата: YYYY-MM-DD» — добавь строку `- **Дата:** 2026-11-09` (это поле обязательно, плейсхолдер не считается)")

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

    # «Плюсы/Минусы» исчерпывающие: один пункт — предупреждение, не ошибка (@TL 02:11).
    blocks = re.split(r"(?im)^###\s+вариант", text)[1:]
    for idx, block in enumerate(blocks, 1):
        for label in ("Плюсы", "Минусы"):
            n = count_items(block, label)
            if n and n < 2:
                warnings.append("вариант %d: «%s» — %d пункт(а), нужен исчерпывающий список"
                                % (idx, label, n))

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
    ap.add_argument("--range", default=None,
                    help="git-диапазон (a..b): проверять только изменённые ADR")
    ap.add_argument("--selftest", action="store_true", help="негативный контроль (D-12)")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    adr_dir = args.dir or os.path.join("04-architecture", "ADL")
    if not os.path.isdir(adr_dir):
        print("check_adr: нет папки %s" % adr_dir)
        return 2

    ranged = None
    if args.range:
        out = subprocess.run(["git", "diff", "--name-only", args.range],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
        if out.returncode != 0:
            print("check_adr: git diff %s не сработал: %s"
                  % (args.range, (out.stderr or "").strip()[:120]))
            return 2
        wanted = {line.strip().split("/")[-1] for line in out.stdout.splitlines() if line.strip()}
        ranged = {n for n in wanted
                  if n.lower().startswith("adr-") and n.lower().endswith(".md")
                  and "template" not in n.lower()}
        if not ranged:
            print("check_adr: в диапазоне %s изменённых ADR нет" % args.range)
            return 0

    since = datetime.date.fromisoformat(args.since)
    checked = skipped = downgraded = 0
    bad = 0
    for name, path in iter_adrs(adr_dir):
        if ranged is not None and name not in ranged:
            continue
        d = None
        dm = DATE_RE.search(open(path, encoding="utf-8-sig").read())
        if dm:
            try:
                d = datetime.date.fromisoformat(dm.group(1))
            except ValueError:
                d = None
        is_old = d is not None and d < since
        if ranged is None and is_old:
            skipped += 1
            continue
        checked += 1
        errors, warnings = check_file(path, args.strict)
        if is_old and errors:
            # Грандфазеринг по дате: правка старого ADR не должна красить гейт.
            # Иначе «исправить одну ссылку в ADR-008» требует переписать весь документ
            # (стимул не трогать доки — класс «доки расходятся с кодом»). @TL 08:41, вариант (б).
            warnings = warnings + [
                "старый ADR (до %s): ошибки понижены до предупреждений — "
                "полный шаблон нужен только при содержательном переписывании"
                % args.since] + errors
            errors = []
            downgraded += 1
        if errors or (args.strict and warnings):
            bad += 1
            print("✗ %s" % name)
            for e in errors:
                print("   ошибка: %s" % e)
            for w in warnings:
                print("   предупреждение: %s" % w)
        elif warnings:
            print("⚠ %s: %s" % (name, "; ".join(warnings)))

    print("check_adr: проверено %d (старых пропущено %d, старых с понижением %d), нарушений %d"
          % (checked, skipped, downgraded, bad))
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
