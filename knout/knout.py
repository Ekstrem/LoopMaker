#!/usr/bin/env python3
"""knout.py — «Кнут» (knout / whip): детектор простоя ролей проекта.

Каждые interval_min минут (по планировщику) смотрит на outbox'ы ролей проекта
и, если доля простаивающих ролей >= idle_ratio, пишет пинг в чат координатору
(@TL), чтобы тот разморозил работу.

«Простаивает» = роль не писала в свой outbox дольше idle_hours (или не писала
вовсе). TL из подсчёта исключён — он адресат, а не «работающая» роль.

Конфиг — loop.config.json, секция "knout" (все поля необязательны, есть defaults):
    project      : "XDiary"    — имя проекта (метка в сообщении)
    idle_hours   : 6           — порог простоя, часов
    idle_ratio   : 0.8         — доля простаивающих (0.8 = 80%)
    interval_min : 15          — период (справочно; cron ставит сам)
    chat_dir     : "5-tech-backlog/chat"
    roles        : ["be","do","fe","te","ar","po","lm"]  — кого считаем
    from_chat    : "5-tech-backlog/chat/lm.md"           — куда писать пинг
    to           : "@TL"       — адресат пинга

Запуск:
    python tools/knout.py --config loop.config.json
    python tools/knout.py --config loop.config.json --dry-run
    python tools/knout.py --selftest
"""
import argparse
import datetime
import json
import os
import re
import sys

STAMP = re.compile(r"^-\s*\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]")

DEFAULTS = {
    "project": "XDiary",
    "idle_hours": 6,
    "idle_ratio": 0.8,
    "interval_min": 15,
    "chat_dir": "5-tech-backlog/chat",
    "roles": ["be", "do", "fe", "te", "ar", "po", "lm"],
    "from_chat": "5-tech-backlog/chat/lm.md",
    "to": "@TL",
}


def load_config(path):
    cfg = dict(DEFAULTS)
    cfg["tz_offset_hours"] = 3
    if path and os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        kn = data.get("knout") or {}
        for k in DEFAULTS:
            if k in kn:
                cfg[k] = kn[k]
        ct = data.get("chat_time") or {}
        cfg["tz_offset_hours"] = int(ct.get("tz_offset_hours", 3))
    return cfg


def last_stamp(path):
    """Последняя валидная метка в outbox (или None, если нет)."""
    best = None
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            m = STAMP.match(line.strip())
            if not m:
                continue
            try:
                ts = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if best is None or ts > best:
                best = ts
    return best


def now_project(cfg):
    return (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            + datetime.timedelta(hours=cfg["tz_offset_hours"]))


def idle_roles(cfg, now):
    idle, active = [], []
    for role in cfg["roles"]:
        last = last_stamp(os.path.join(cfg["chat_dir"], role + ".md"))
        if last is None or (now - last) > datetime.timedelta(hours=cfg["idle_hours"]):
            idle.append((role, last))
        else:
            active.append((role, last))
    return idle, active


def ping_message(cfg, now, idle, active):
    total = len(idle) + len(active)
    names = " " + ", ".join("@" + r.upper() for r, _ in idle) if idle else ""
    return ("- [%s] %s: ⚠️ Кнут (%s): %d/%d ролей простаивают > %d ч.%s"
            % (now.strftime("%Y-%m-%d %H:%M"), cfg["to"], cfg["project"],
               len(idle), total, cfg["idle_hours"], names))


def selftest():
    import tempfile
    ok = True
    # 1) defaults загружаются без конфига
    cfg = load_config(None)
    ok &= cfg["project"] == "XDiary" and cfg["idle_ratio"] == 0.8
    # 2) last_stamp: битые метки (час>23) пропускаются, валидная берётся
    d = tempfile.mkdtemp()
    p = os.path.join(d, "be.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("- [2026-11-04 25:00] → @TL: битая\n")
        f.write("- [2026-11-04 07:00] → @TL: валидная\n")
    ok &= last_stamp(p) == datetime.datetime(2026, 11, 4, 7, 0)
    # 3) пустой outbox = нет метки
    ok &= last_stamp(os.path.join(d, "do.md")) is None
    # 4) порог: 1 из 2 простаивает = 50% < 80%
    cfg2 = dict(DEFAULTS); cfg2["chat_dir"] = d; cfg2["roles"] = ["be", "do"]
    now = datetime.datetime(2026, 11, 4, 12, 0)
    idle, active = idle_roles(cfg2, now)
    ok &= len(idle) == 1 and len(active) == 1
    print("selftest:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Кнут — детектор простоя ролей")
    ap.add_argument("--config", default=None)
    ap.add_argument("--dry-run", action="store_true", help="не писать, только показать")
    ap.add_argument("--selftest", action="store_true", help="самопроверка")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.selftest:
        return selftest()

    cfg = load_config(args.config)
    now = now_project(cfg)
    idle, active = idle_roles(cfg, now)
    total = len(idle) + len(active)
    ratio = len(idle) / max(1, total)

    print("%s: простаивают %d/%d (%.0f%%)" % (cfg["project"], len(idle), total, ratio * 100))
    for role, last in idle:
        print("  @%s: %s" % (role.upper(),
                             last.strftime("%Y-%m-%d %H:%M") if last else "нет сообщений"))

    if ratio < cfg["idle_ratio"]:
        print("ниже порога %.0f%% — пинг не нужен" % (cfg["idle_ratio"] * 100))
        return 0

    msg = ping_message(cfg, now, idle, active)
    print("ПИНГ: " + msg)
    if args.dry_run:
        return 0

    with open(cfg["from_chat"], "a", encoding="utf-8") as f:
        f.write("\n" + msg + "\n")
    print("записано в %s" % cfg["from_chat"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
