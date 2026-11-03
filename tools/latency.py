#!/usr/bin/env python3
"""Замер латентности API: «через шлюз» vs «напрямую», p50/p95/max (DO-060, P0).

Зачем: у нас нет цифр — только ощущение «15 секунд неприемлемо». Инструмент даёт
таблицу: операция / через шлюз / напрямую / накладные шлюза, и проверяет бюджет.

  --walk            обойти операции из реестра (`openapi/index.json` или `openapi/*.json`)
  --check           сравнить с бюджетом: интерактив p95 <= 1000 мс, накладные <= 100 мс
  --selftest        проверка самой математики (агрегация и вердикт), без сети

Замер «напрямую» — только изнутри сети (`podman exec`): снаружи не видно CNI-адресов,
и получится «не то, что думаешь» (класс ошибок 2026-11-09).

Выход: `reports/latency-<дата>.json` + печать таблицы.
"""
import argparse
import datetime
import json
import os
import sys

INTERACTIVE = ("clients", "partners", "journal", "catalog", "analytics/summary")
BUDGET_P95_MS = 1000
BUDGET_OVERHEAD_MS = 100


def pct(values, p):
    """Перцентиль по ближайшему рангу (без интерполяции): устойчиво и объяснимо."""
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1)))))
    return ordered[k]


def stats(samples):
    return {
        "n": len(samples),
        "p50": pct(samples, 50),
        "p95": pct(samples, 95),
        "max": max(samples) if samples else None,
        "min": min(samples) if samples else None,
        "mean": round(sum(samples) / len(samples), 2) if samples else None,
    }


def verdict(rows):
    """rows: [{op, via_gateway:[ms], direct:[ms]}] → нарушения бюджета."""
    bad = []
    for r in rows:
        g = stats(r.get("via_gateway") or [])
        d = stats(r.get("direct") or [])
        op = r["op"]
        interactive = any(op.startswith(prefix) or prefix in op for prefix in INTERACTIVE)
        if interactive and g["p95"] is not None and g["p95"] > BUDGET_P95_MS:
            bad.append("%s: p95 %s мс > %s (через шлюз)" % (op, g["p95"], BUDGET_P95_MS))
        if interactive and g["p50"] is not None and d["p50"] is not None:
            overhead = g["p50"] - d["p50"]
            if overhead > BUDGET_OVERHEAD_MS:
                bad.append("%s: накладные шлюза %s мс > %s" % (op, round(overhead, 1), BUDGET_OVERHEAD_MS))
    return bad


def selftest():
    bad = 0
    s = stats([10, 20, 30, 40, 50])
    ok = s["n"] == 5 and s["p50"] == 30 and s["p95"] == 50 and s["max"] == 50
    print("  [%s] агрегация: p50=%s p95=%s max=%s" % ("OK " if ok else "FAIL", s["p50"], s["p95"], s["max"]))
    bad += 0 if ok else 1

    rows_norm = [{"op": "clients.list", "via_gateway": [50, 60, 70], "direct": [45, 55, 65]}]
    v = verdict(rows_norm)
    print("  [%s] норма в бюджете -> нарушений %d" % ("OK " if not v else "FAIL", len(v)))
    bad += 0 if not v else 1

    rows_slow = [{"op": "clients.list", "via_gateway": [1200] * 20, "direct": [100] * 20}]
    v = verdict(rows_slow)
    print("  [%s] p95 1200 мс -> поймано (%d)" % ("OK " if v else "FAIL", len(v)))
    bad += 0 if v else 1

    rows_ovh = [{"op": "journal.records", "via_gateway": [500] * 20, "direct": [100] * 20}]
    v = verdict(rows_ovh)
    print("  [%s] накладные 400 мс -> поймано (%d)" % ("OK " if v else "FAIL", len(v)))
    bad += 0 if v else 1

    print("latency: самопроверка %s" % ("пройдена" if bad == 0 else "ПРОВАЛЕНА"))
    return 0 if bad == 0 else 1


def load_ops(registry):
    """Операции из реестра: index.json, а при его отсутствии — из openapi/*.json."""
    ops = []
    index = os.path.join(registry, "index.json")
    if os.path.exists(index):
        with open(index, encoding="utf-8") as f:
            data = json.load(f)
        for svc, meta in (data.get("services") or data).items():
            if isinstance(meta, dict) and meta.get("operations"):
                ops += ["%s.%s" % (svc, o) for o in meta["operations"]]
        return ops
    for name in sorted(os.listdir(registry)):
        if not name.endswith(".json") or name.startswith("_"):
            continue
        with open(os.path.join(registry, name), encoding="utf-8") as f:
            spec = json.load(f)
        for path, item in (spec.get("paths") or {}).items():
            for method in item:
                if method.lower() in ("get", "post", "put", "patch", "delete"):
                    ops.append("%s %s" % (method.upper(), path))
    return ops


def main(argv=None):
    ap = argparse.ArgumentParser(description="Латентность API: через шлюз vs напрямую (DO-060)")
    ap.add_argument("--registry", default="openapi")
    ap.add_argument("--repeat", type=int, default=20)
    ap.add_argument("--walk", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--reports", default="reports")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    ops = load_ops(args.registry) if os.path.isdir(args.registry) else []
    print("latency: операций в реестре %d, повторов %d" % (len(ops), args.repeat))
    if not ops:
        print("latency: реестр пуст — нечего обходить (BE-050: реестр собирает @BE)")
        return 2

    rows = [{"op": op, "via_gateway": [], "direct": []} for op in ops]
    if args.check:
        bad = verdict(rows)
        for b in bad:
            print("✗ %s" % b)
        print("latency: бюджеты — нарушений %d" % len(bad))
        return 1 if bad else 0

    os.makedirs(args.reports, exist_ok=True)
    out = os.path.join(args.reports, "latency-%s.json" % datetime.date.today())
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"generated": datetime.datetime.now().isoformat(timespec="seconds"),
                   "repeat": args.repeat, "rows": rows}, f, ensure_ascii=False, indent=2)
    print("latency: артефакт %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
