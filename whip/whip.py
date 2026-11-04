#!/usr/bin/env python3
"""whip.py — «Кнут» (whip): детектор простоя ролей проекта.

Каждые interval_min минут (по планировщику) смотрит на outbox'ы ролей проекта
и, если доля простаивающих ролей >= idle_ratio, пишет пинг в чат координатору
(@TL), чтобы тот разморозил работу.

«Простаивает» = роль не писала в свой outbox дольше idle_minutes (по умолчанию
равно интервалу: не писал с прошлой проверки). TL из подсчёта исключён — он
адресат, а не «работающая» роль.

Конфиг — loop.config.json, секция "whip" (все поля необязательны, есть defaults):
    project       : "XDiary"    — имя проекта (метка в сообщении)
    idle_minutes  : 15          — роль «простаивает», если не писала дольше
    idle_ratio    : 0.8         — доля простаивающих (0.8 = 80%)
    interval_min  : 15          — период (справочно; cron ставит сам)
    chat_dir      : "5-tech-backlog/chat"
    roles         : ["be","do","fe","te","ar","po","lm"]  — кого считаем
    from_chat     : "5-tech-backlog/chat/lm.md"           — куда писать пинг
    to            : "@TL"       — адресат пинга

Запуск:
    python tools/whip.py --config loop.config.json
    python tools/whip.py --config loop.config.json --dry-run
    python tools/whip.py --selftest
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

STAMP = re.compile(r"^-\s*\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]")

DEFAULTS = {
    "project": "XDiary",
    "idle_minutes": 15,
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
        wh = data.get("whip") or {}
        for k in DEFAULTS:
            if k in wh:
                cfg[k] = wh[k]
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


def last_commit_ts(path):
    """Время последнего коммита файла (реальные машинные часы, не шкала чата)."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct", "--", path],
                             capture_output=True, text=True).stdout.strip()
        if out.isdigit():
            return datetime.datetime.fromtimestamp(int(out))
    except Exception:
        pass
    return None

def now_project(cfg):
    return (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            + datetime.timedelta(hours=cfg["tz_offset_hours"]))


def idle_roles(cfg, now):
    idle, active = [], []
    for role in cfg["roles"]:
        last = last_commit_ts(os.path.join(cfg["chat_dir"], role + ".md"))
        if last is None or (now - last) > datetime.timedelta(minutes=cfg["idle_minutes"]):
            idle.append((role, last))
        else:
            active.append((role, last))
    return idle, active


def ping_message(cfg, now, idle, active):
    total = len(idle) + len(active)
    names = " " + ", ".join("@" + r.upper() for r, _ in idle) if idle else ""
    return ("- [%s] %s: ⚠️ Кнут (%s): %d/%d ролей простаивают > %d мин.%s"
            % (now.strftime("%Y-%m-%d %H:%M"), cfg["to"], cfg["project"],
               len(idle), total, cfg["idle_minutes"], names))


def selftest():
    ok = True
    cfg = load_config(None)
    ok &= cfg["project"] == "XDiary" and cfg["idle_ratio"] == 0.8
    ok &= cfg["idle_minutes"] == 15 and cfg["interval_min"] == 15
    # last_commit_ts на реальном git-файле (сам whip.py) — не None
    ok &= last_commit_ts("tools/whip.py") is not None
    # ping_message: 1/2 = 50% (< 80%), но текст несёт Кнут + адресата + счёт
    msg = ping_message(cfg, datetime.datetime(2026, 11, 4, 10, 0), [("be", None)], [("do", None)])
    ok &= "Кнут" in msg and "1/2" in msg and "@TL" in msg
    print("selftest:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Кнут (whip) — детектор простоя ролей")
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
