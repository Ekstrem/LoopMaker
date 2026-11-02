"""Гейт карты тестов: каждый тестовый проект упомянут в README тестов, и наоборот.

Зачем: правило «имя проекта ≠ имя сервиса, вывод о покрытии — только по grep» живёт
в `9-tests/README.md`; без гейта таблица протухает в тот день, когда появился новый
проект (или удалён старый), и снова начинаются выводы «по названию».

    python tools/check_test_map.py            # код 1, если карта разошлась
    python tools/check_test_map.py --report   # только показать
"""
import argparse
import glob
import json
import os
import re
import sys

CONFIG = "loop.config.json"
DEFAULT = {"glob": "9-tests/**/*.csproj", "readme": "9-tests/README.md"}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Карта «проект → сервисы» актуальна")
    ap.add_argument("--root", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    root = args.root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg_path = args.config or os.path.join(root, CONFIG)
    mapping = dict(DEFAULT)
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        mapping.update(cfg.get("test_map") or {})

    readme_path = os.path.join(root, mapping["readme"])
    if not os.path.exists(readme_path):
        print("check_test_map: нет %s" % mapping["readme"])
        return 1
    with open(readme_path, encoding="utf-8-sig") as f:
        readme = f.read()

    projects = sorted({os.path.basename(os.path.dirname(p))
                       for p in glob.glob(os.path.join(root, mapping["glob"]), recursive=True)})
    missing = [p for p in projects if p not in readme]

    # строки таблицы вида `| `Project.Name` | ...|` без существующего проекта — протухшие
    mentioned = set(re.findall(r"`([A-Za-z]+\.(?:Integration)?Tests)`", readme))
    stale = sorted(mentioned - set(projects))

    print("check_test_map: проектов %d, упомянуто в README %d" % (len(projects), len(projects) - len(missing)))
    for name in missing:
        print("  нет в README:", name)
    for name in stale:
        print("  упомянут, но проекта нет:", name)

    if (missing or stale) and not args.report:
        print("")
        print("Правило: имя проекта ≠ имя сервиса; вывод о покрытии — только по `git grep`.")
        print("Таблица живёт в %s, обнови её тем же коммитом." % mapping["readme"])
        return 1
    print("check_test_map: карта актуальна")
    return 0


if __name__ == "__main__":
    sys.exit(main())
