#!/usr/bin/env python3
"""Дрейф и breaking-change для реестра контрактов `openapi/` (ADR-023, BE-050).

Две проверки, обе — без сети и без стенда (это принципиально: гейт не должен
зависеть от того, включён ли swagger в конкретном окружении):

  1) дрейф:      `--registry openapi --generated <dir>` — реестр в git против
                 свежесгенерированных спек. Любое расхождение → rc=1.
  2) breaking:   `--registry openapi --base <git-rev>` — реестр сейчас против
                 реестра в указанном коммите. Удалённый путь/метод, удалённый
                 обязательный параметр, суженный enum → rc=1 (breaking);
                 добавления → rc=0 (аддитивно).

Почему путь реестра — параметр: каталог реестра может переехать (BE-050 инкремент
2 — генерация в CI), и гейт не должен из-за этого переписываться.

Проверка самого гейта — `--selftest` (двусторонняя: совпадает → 0, расходится → 1).
"""
import argparse
import json
import os
import subprocess
import sys
import shutil


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def registry_files(registry):
    return sorted(f for f in os.listdir(registry) if f.endswith(".json") and not f.startswith("_"))


def ops_of(spec):
    """{(path, method): operation} — только методы HTTP, без параметров/сумм."""
    out = {}
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() in ("get", "post", "put", "patch", "delete", "head", "options"):
                out[(path, method.lower())] = op or {}
    return out


def required_params(spec, path, method):
    op = ops_of(spec).get((path, method), {})
    names = set()
    for p in (op.get("parameters") or []):
        if p.get("required"):
            names.add((p.get("in", "?"), p.get("name", "?")))
    body = (op.get("requestBody") or {})
    if body.get("required"):
        names.add(("body", "*"))
    return names


def enums(spec):
    """{место: множество значений} — по схеме; грубо, но для сужения достаточно."""
    out = {}
    for name, schema in (spec.get("components", {}).get("schemas") or {}).items():
        for field, props in (schema.get("properties") or {}).items():
            if isinstance(props, dict) and isinstance(props.get("enum"), list):
                out[(name, field)] = set(map(str, props["enum"]))
    return out


def diff_specs(a, b, label):
    """a — старое, b — новое. Возвращает список breaking-находок."""
    problems = []
    oa, ob = ops_of(a), ops_of(b)
    for key in sorted(set(oa) - set(ob)):
        problems.append("%s: удалён маршрут %s %s" % (label, key[1].upper(), key[0]))
    for key in sorted(set(oa) & set(ob)):
        lost = required_params(a, *key) - required_params(b, *key)
        for where, name in sorted(lost):
            problems.append("%s: %s %s — перестал быть обязательным параметр %s:%s"
                            % (label, key[1].upper(), key[0], where, name))
    for where, values in sorted(enums(a).items()):
        new = enums(b).get(where)
        if new is not None and not values.issubset(new):
            problems.append("%s: сужен enum %s.%s: убраны %s"
                            % (label, where[0], where[1], sorted(values - new)))
    return problems


def check_drift(registry, generated):
    problems = []
    reg = set(registry_files(registry))
    gen = set(registry_files(generated))
    for missing in sorted(reg - gen):
        problems.append("в сгенерированных нет %s (реестр его знает)" % missing)
    for extra in sorted(gen - reg):
        problems.append("в реестре нет %s (сгенерированный появился, реестр не обновлён)" % extra)
    for name in sorted(reg & gen):
        a, b = load_json(os.path.join(registry, name)), load_json(os.path.join(generated, name))
        if ops_of(a) != ops_of(b):
            problems.append("%s: расходится набор маршрутов/методов (реестр vs генерация)" % name)
    return problems


def check_breaking(registry, base_rev):
    problems = []
    for name in registry_files(registry):
        cur = load_json(os.path.join(registry, name))
        r = subprocess.run(["git", "show", "%s:%s/%s" % (base_rev, registry.replace("\\", "/"), name)],
                           capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            continue  # файла в базовой ревизии не было — это добавление, не breaking
        try:
            old = json.loads(r.stdout)
        except Exception:
            problems.append("%s: в %s невалидный JSON — сравнение невозможно" % (name, base_rev))
            continue
        problems += diff_specs(old, cur, name)
    return problems


def selftest():
    bad = 0
    tmp = os.path.join(os.getcwd(), ".tmp-check-openapi")
    shutil.rmtree(tmp, ignore_errors=True)
    reg = os.path.join(tmp, "reg")
    gen = os.path.join(tmp, "gen")
    os.makedirs(reg)
    os.makedirs(gen)

    spec = {"paths": {"/api/v1/x": {"get": {"parameters": [
        {"name": "id", "in": "query", "required": True}]}}},
        "components": {"schemas": {"S": {"properties": {"g": {"enum": ["male", "female"]}}}}}}
    for d in (reg, gen):
        with open(os.path.join(d, "svc.json"), "w", encoding="utf-8") as f:
            json.dump(spec, f)

    p = check_drift(reg, gen)
    print("  [%s] одинаковые реестры -> нарушений %d" % ("OK " if not p else "FAIL", len(p)))
    bad += 0 if not p else 1

    changed = json.loads(json.dumps(spec))
    del changed["paths"]["/api/v1/x"]["get"]
    with open(os.path.join(gen, "svc.json"), "w", encoding="utf-8") as f:
        json.dump(changed, f)
    p = check_drift(reg, gen)
    print("  [%s] маршрут удалён -> поймано (%d)" % ("OK " if p else "FAIL", len(p)))
    bad += 0 if p else 1

    narrow = json.loads(json.dumps(spec))
    narrow["components"]["schemas"]["S"]["properties"]["g"]["enum"] = ["male"]
    p = diff_specs(spec, narrow, "svc.json")
    print("  [%s] сужен enum -> поймано (%d)" % ("OK " if p else "FAIL", len(p)))
    bad += 0 if p else 1

    add = json.loads(json.dumps(spec))
    add["paths"]["/api/v1/new"] = {"post": {}}
    p = diff_specs(spec, add, "svc.json")
    print("  [%s] добавлен маршрут -> НЕ breaking (находок %d)" % ("OK " if not p else "FAIL", len(p)))
    bad += 0 if not p else 1

    shutil.rmtree(tmp, ignore_errors=True)
    print("check_openapi: самопроверка %s" % ("пройдена" if bad == 0 else "ПРОВАЛЕНА"))
    return 0 if bad == 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Дрейф и breaking-change реестра openapi/")
    ap.add_argument("--registry", default="openapi", help="каталог реестра (параметр, не константа)")
    ap.add_argument("--generated", help="каталог со свежесгенерированными спеками (режим дрейфа)")
    ap.add_argument("--base", help="git-ревизия для сравнения (режим breaking-change)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not os.path.isdir(args.registry):
        print("check_openapi: нет каталога реестра %r" % args.registry)
        return 2
    if args.generated and args.base:
        print("check_openapi: выбери одно — --generated или --base")
        return 2

    if args.generated:
        problems = check_drift(args.registry, args.generated)
        kind = "дрейф"
    elif args.base:
        problems = check_breaking(args.registry, args.base)
        kind = "breaking-change"
    else:
        print("check_openapi: нужен --generated или --base (или --selftest)")
        return 2

    for p in problems:
        print("✗ %s" % p)
    print("check_openapi: %s — нарушений %d (файлов %d)"
          % (kind, len(problems), len(registry_files(args.registry))))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
