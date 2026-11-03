#!/usr/bin/env python3
"""Один источник инструментов петли: сверка и синхронизация копий.

Проблема: инструменты живут в LoopMaker (шаблон) и копируются в проект (XDiary),
потому что проект — публичный, а LoopMaker приватный: CI проекта не может его
чекаутить. Копии расходятся молча (так уже было с `check_paths`, `stats`, `sweep`).

Решение: манифест `tools/.sources.json` хранит sha256 тела каждого общего скрипта
на момент последней синхронизации.

  --check   (CI) файл правлен без sync → код 1. Ловит немаркированную правку.
  --report  сравнить с локальным LoopMaker (`--from`), показать расхождения.
  --pull    скопировать из LoopMaker (с шапкой «КОПИЯ») и обновить манифест.
  --add N   добавить инструмент в манифест (сразу с текущим состоянием).

Сравнение — по «телу»: без BOM, без первой строки-шапки «КОПИЯ из LoopMaker»,
с нормализованными переводами строк. Поэтому копия может отличаться шапкой и не
считаться расхождением.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date

MANIFEST = os.path.join("tools", ".sources.json")
# шапку снимаем где угодно в первых трёх строках: у копий она идёт после shebang
HEADER_RE = re.compile(r"(?m)^# КОПИЯ из LoopMaker[^\n]*\n")
HEADER = "# КОПИЯ из LoopMaker/{name} (источник правды там; правка — в обоих местах)."


def root(arg=None):
    if arg:
        return os.path.abspath(arg)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n")


def body(text):
    out = HEADER_RE.sub("", text, count=1)
    return out.strip("\n") + "\n"


def with_header(name, text):
    """Копия = shebang (если есть) + шапка + тело."""
    body_text = body(text)
    if body_text.startswith("#!"):
        first, _, rest = body_text.partition("\n")
        return first + "\n" + HEADER.format(name=name) + "\n" + rest
    return HEADER.format(name=name) + "\n" + body_text


def digest(text):
    return hashlib.sha256(body(text).encode("utf-8")).hexdigest()


def load_manifest(root_dir):
    path = os.path.join(root_dir, MANIFEST)
    if not os.path.exists(path):
        return {"upstream": "Ekstrem/LoopMaker", "tools": {}}
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def save_manifest(root_dir, data):
    path = os.path.join(root_dir, MANIFEST)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def local_path(root_dir, name):
    return os.path.join(root_dir, "tools", name)


def upstream_path(upstream_dir, name):
    return os.path.join(upstream_dir, "tools", name)


def cmd_check(root_dir):
    data = load_manifest(root_dir)
    tools = data.get("tools") or {}
    if not tools:
        print("sync_tools: манифест пуст — нечего проверять")
        return 0
    bad = []
    for name, meta in sorted(tools.items()):
        path = local_path(root_dir, name)
        if not os.path.exists(path):
            bad.append((name, "файла нет"))
            continue
        if digest(read(path)) != meta.get("sha256"):
            bad.append((name, "правлен без sync"))
    if not bad:
        print("sync_tools: %d инструментов совпадают с манифестом" % len(tools))
        return 0
    print("sync_tools: расходжение с манифестом (%d):" % len(bad))
    for name, why in bad:
        print("  - %s: %s" % (name, why))
    print("")
    print("Если правка нужна и в проекте, и в шаблоне:")
    print("  1) перенести правку в LoopMaker;")
    print("  2) python tools/sync_tools.py --pull --from ../LoopMaker;")
    print("  3) закоммитить tools/.sources.json вместе с копиями.")
    return 1


def cmd_report(root_dir, upstream_dir):
    data = load_manifest(root_dir)
    tools = data.get("tools") or {}
    if not os.path.isdir(upstream_dir):
        print("sync_tools: рядом нет %s — сравнить не с чем (в CI это норма)" % upstream_dir)
        return 0
    drift = []
    for name in sorted(tools):
        up = upstream_path(upstream_dir, name)
        loc = local_path(root_dir, name)
        if not os.path.exists(up):
            drift.append((name, "нет в шаблоне"))
        elif not os.path.exists(loc):
            drift.append((name, "нет в проекте"))
        elif digest(read(loc)) != digest(read(up)):
            drift.append((name, "расходится с шаблоном"))
    if not drift:
        print("sync_tools: все %d копий совпадают с шаблоном" % len(tools))
        return 0
    print("sync_tools: расхождения с шаблоном (%d из %d):" % (len(drift), len(tools)))
    for name, why in drift:
        print("  ~ %s: %s" % (name, why))
    print("")
    print("Синхронизировать: python tools/sync_tools.py --pull --from %s" % upstream_dir)
    return 0


def cmd_pull(root_dir, upstream_dir, only=None, names=None, force=False):
    data = load_manifest(root_dir)
    tools = data.get("tools") or {}
    if names:
        for n in names:
            tools.setdefault(n, {})
        data["tools"] = tools
    todo = [only] if only else (names or sorted(tools))

    # guard (D-10): --pull молча затирает локальные правки, если их не перенесли в источник.
    # Отказываем, если копия правлена после последней синхронизации (хеш ≠ манифест),
    # и это не новый инструмент без записи в манифесте.
    dirty = []
    for name in todo:
        path = local_path(root_dir, name)
        meta = tools.get(name) or {}
        if os.path.exists(path) and meta.get("sha256") and digest(read(path)) != meta["sha256"]:
            dirty.append(name)
    if dirty and not force:
        print("sync_tools: локальные правки не выгружены (%d) — pull отказался:" % len(dirty))
        for n in dirty:
            print("  - %s: правлен без sync" % n)
        print("")
        print("Перенеси правку в источник (LoopMaker), затем повтори pull;")
        print("или осознанно затёрть копию источником: python tools/sync_tools.py --pull --force")
        return 1

    changed = 0
    for name in todo:
        up = upstream_path(upstream_dir, name)
        if not os.path.exists(up):
            print("  ! %s: нет в шаблоне" % name)
            continue
        text = read(up)
        with open(local_path(root_dir, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(with_header(name, text))
        tools[name] = {"sha256": digest(read(up)), "synced_at": date.today().isoformat(),
                       "upstream": "tools/" + name}
        changed += 1
    data["tools"] = tools
    data.setdefault("upstream", "Ekstrem/LoopMaker")
    save_manifest(root_dir, data)
    print("sync_tools: обновлено копий: %d, манифест перезаписан" % changed)
    return 0


def cmd_seed(root_dir, names):
    """Зафиксировать текущее состояние как базовое, ничего не копируя."""
    data = load_manifest(root_dir)
    tools = data.get("tools") or {}
    for name in names:
        path = local_path(root_dir, name)
        if not os.path.exists(path):
            print("  ! %s: файла нет" % name)
            continue
        tools[name] = {"sha256": digest(read(path)), "synced_at": date.today().isoformat(),
                       "upstream": "tools/" + name}
    data["tools"] = tools
    data.setdefault("upstream", "Ekstrem/LoopMaker")
    save_manifest(root_dir, data)
    print("sync_tools: в манифесте инструментов: %d" % len(tools))
    return 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Сверка копий инструментов с манифестом")
    ap.add_argument("--root", default=None)
    ap.add_argument("--from", dest="upstream", default=None, help="путь к LoopMaker")
    ap.add_argument("--check", action="store_true", help="CI: копии совпадают с манифестом")
    ap.add_argument("--report", action="store_true", help="сравнить с локальным LoopMaker")
    ap.add_argument("--pull", action="store_true", help="скопировать из LoopMaker")
    ap.add_argument("--force", action="store_true",
                    help="с --pull: затереть локальные правки копии источником (осознанно)")
    ap.add_argument("--seed", action="store_true", help="зафиксировать текущее состояние в манифест")
    ap.add_argument("--add", default=None, help="добавить инструмент в манифест")
    ap.add_argument("--names", default=None, help="список через запятую: какие инструменты брать")
    args = ap.parse_args()

    root_dir = root(args.root)
    upstream = args.upstream or os.path.join(os.path.dirname(root_dir), "LoopMaker")
    names = [n.strip() for n in (args.names or "").split(",") if n.strip()]

    if args.check:
        return cmd_check(root_dir)
    if args.seed:
        return cmd_seed(root_dir, names or [n for n in sorted(os.listdir(os.path.join(root_dir, "tools")))
                                           if n.endswith(".py")])
    if args.pull:
        return cmd_pull(root_dir, upstream, args.add, names or None, force=args.force)
    if args.add:
        return cmd_pull(root_dir, upstream, args.add, force=args.force)
    return cmd_report(root_dir, upstream)


if __name__ == "__main__":
    sys.exit(main())
