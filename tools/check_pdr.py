"""Линтер PDR: каждое решение — файл с обязательными полями, индекс актуален.

Требования формата (из `docs/pdr/README.md`): варианты (≥ 2), владелец, согласования,
критерий/метрика. Без этого PDR превращается в «мы решили», и обсуждать нечего.

    python tools/check_pdr.py            # код 1 при нарушении формата
    python tools/check_pdr.py --report   # только показать
"""
import argparse
import glob
import os
import re
import sys

REQUIRED = ["Статус", "Владелец", "Согласования"]
OPTIONS_HEAD = "## Варианты"
CRITERIA = ("## Критерий", "## Критерий приёмки")
STATUSES = ("принято", "обсуждается", "отклонено", "заменено")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Формат PDR и актуальность индекса")
    ap.add_argument("--root", default=None)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    root = args.root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pdr_dir = os.path.join(root, "docs", "pdr")
    if not os.path.isdir(pdr_dir):
        print("check_pdr: нет папки docs/pdr")
        return 1

    index_path = os.path.join(pdr_dir, "README.md")
    index = open(index_path, encoding="utf-8-sig").read() if os.path.exists(index_path) else ""

    problems = []
    files = sorted(glob.glob(os.path.join(pdr_dir, "PDR-*.md")))
    for path in files:
        name = os.path.basename(path)
        body = open(path, encoding="utf-8-sig").read()
        for field in REQUIRED:
            if "| **%s** |" % field not in body:
                problems.append("%s: нет поля «%s»" % (name, field))
        if not re.search(r"^\|\s*\*\*Статус\*\*\s*\|\s*(\w+)", body, re.M):
            problems.append("%s: статус не читается" % name)
        else:
            status = re.search(r"\*\*Статус\*\*\s*\|\s*([^|]+)", body).group(1).strip()
            if not any(s in status for s in STATUSES):
                problems.append("%s: неизвестный статус «%s»" % (name, status))
        if OPTIONS_HEAD not in body:
            problems.append("%s: нет раздела «Варианты»" % name)
        if not any(c in body for c in CRITERIA):
            problems.append("%s: нет раздела «Критерий»/«Метрика»" % name)
        if name not in index:
            problems.append("%s: нет строки в индексе docs/pdr/README.md" % name)

    # обратная сторона: индекс ссылается на несуществующий файл
    for link in set(re.findall(r"\((PDR-\d+\.md)\)", index)):
        if not os.path.exists(os.path.join(pdr_dir, link)):
            problems.append("индекс ссылается на отсутствующий %s" % link)

    print("check_pdr: файлов %d, нарушений %d" % (len(files), len(problems)))
    for item in problems[:30]:
        print("  ", item)
    if problems and not args.report:
        print("")
        print("Формат: docs/pdr/README.md и TEMPLATE.md (варианты, владелец, согласования, критерий).")
        return 1
    print("check_pdr: формат и индекс в порядке")
    return 0


if __name__ == "__main__":
    sys.exit(main())
