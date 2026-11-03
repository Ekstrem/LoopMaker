#!/usr/bin/env python3
# КОПИЯ из LoopMaker/latency.py (источник правды там; правка — в обоих местах).
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
import statistics
import sys
import time
import urllib.error
import urllib.request

INTERACTIVE = ("clients", "partners", "journal", "catalog", "analytics/summary")

# Учёт «пустой реестр» и «нет замеров» как ОШИБКИ, а не как зелёный: гейт, который
# молчит на отсутствии данных, — дефект гейта (2026-11-12, DO-068-класс).
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


def probe(url, method="GET", timeout=10):
    """Один запрос; возвращает (код, мс). Код 0 = сеть/таймаут."""
    req = urllib.request.Request(url, method=method)
    body = None
    if method == "POST":
        req.add_header("Content-Type", "application/json")
        body = b"{}"
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, data=body, timeout=timeout) as r:
            r.read(2048)
            code = r.status
    except urllib.error.HTTPError as e:
        e.read(2048)
        code = e.code
    except Exception:
        code = 0
    return code, (time.perf_counter() - t0) * 1000.0


def summarize(times):
    if not times:
        return {"n": 0, "p50": None, "p95": None, "max": None}
    ordered = sorted(times)
    return {"n": len(times),
            "p50": round(pct(ordered, 50), 1),
            "p95": round(pct(ordered, 95), 1),
            "max": round(max(ordered), 1)}


def measure(ops, gateway, direct, repeat):
    rows = []
    for op in ops:
        method, path = op.split(" ", 1)
        path = path.replace("{id}", "00000000-0000-0000-0000-000000000000")
        gw, dr = [], []
        for _ in range(repeat):
            c, ms = probe(gateway.rstrip("/") + path, method)
            if c:
                gw.append(ms)
            if direct:
                c2, ms2 = probe(direct.rstrip("/") + path, method)
                if c2:
                    dr.append(ms2)
        g = summarize(gw)
        d = summarize(dr)
        overhead = (round(g["p50"] - d["p50"], 1)
                    if g["p50"] is not None and d["p50"] is not None else None)
        # via_gateway/direct — СЫРЫЕ списки мс (их ждёт verdict/stats),
        # gw/dr — сводки для печати. Смешивать нельзя: verdict(stats) ждёт список.
        rows.append({"op": op, "via_gateway": gw, "direct": dr,
                     "gw": g, "dr": d, "overhead_p50": overhead})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="Латентность API: через шлюз vs напрямую (DO-060)")
    ap.add_argument("--registry", default="openapi")
    ap.add_argument("--repeat", type=int, default=20)
    ap.add_argument("--walk", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--reports", default="reports")
    ap.add_argument("--gateway", default=os.environ.get("XDIARY_GATEWAY", "http://127.0.0.1:8080"))
    ap.add_argument("--direct", default=os.environ.get("XDIARY_DIRECT", ""))
    ap.add_argument("--limit", type=int, default=0, help="ограничить число операций")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    ops = load_ops(args.registry) if os.path.isdir(args.registry) else []
    print("latency: операций в реестре %d, повторов %d" % (len(ops), args.repeat))
    if not ops:
        print("latency: реестр пуст — нечего обходить (BE-050: реестр собирает @BE)")
        return 2

    if args.limit:
        ops = ops[: args.limit]
    rows = measure(ops, args.gateway, args.direct or None, args.repeat)
    measured = [r for r in rows if r["gw"]["n"]]
    print("%-46s %8s %8s %8s %8s" % ("операция", "gw p50", "gw p95", "dir p50", "надб."))
    for r in rows:
        g, d = r["gw"], r["dr"]
        print("%-46s %8s %8s %8s %8s" % (
            r["op"][:46],
            g["p50"] if g["p50"] is not None else "-",
            g["p95"] if g["p95"] is not None else "-",
            d["p50"] if d["p50"] is not None else "-",
            r["overhead_p50"] if r["overhead_p50"] is not None else "-"))
    if not measured:
        print("latency: ни одного успешного замера — это ОШИБКА, а не «нарушений 0»")
        return 2
    if args.check:
        bad = verdict(rows)
        for b in bad:
            print("✗ %s" % b)
        print("latency: замеров %d из %d операций, нарушений бюджета %d"
              % (len(measured), len(rows), len(bad)))
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
