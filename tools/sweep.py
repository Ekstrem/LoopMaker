#!/usr/bin/env python3
"""Обход петли: что стоит без движения и где «обещано, но не начато».

Обычный CI срабатывает на push и поэтому не видит *время*: задача может стоять
«На проверке» сутками, и никто не покраснеет. Плюс есть сбой, который не виден
и по коду: агент заканчивает ход планом («беру BE-014, потом BE-015») без единого
tool call — файлы не меняются, а со стороны кажется, что работа идёт.

Скрипт читает карту проекта `loop.config.json` (ключи: `backlog`, `chat`,
`task_folders`, `statuses`, `role_titles`) и проверяет:

  1. «На проверке» дольше N часов;
  2. «В работе» дольше M часов;
  3. «Закрыта» без блока `## Результат`;
  4. статус вне словаря (синонимы «Выполнена»/«Готово»/«Done»);
  5. «обещано, но не начато» — в чате есть «беру XX-001», а статус задачи
     всё ещё первый (обычно `Открыта`).

Использование:
    python tools/sweep.py --config loop.config.json
    python tools/sweep.py --review-hours 12 --work-hours 48 --claim-hours 0.25
    python tools/sweep.py --json
    python tools/sweep.py --fail-on-stale      # код 1, если есть находки

Коды возврата: 0 — отчёт построен (даже если есть находки), 1 — только с --fail-on-stale.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

CONFIG = "loop.config.json"
TASK_ID = re.compile(r"^(?:[A-Z]{2,3})-\d{2,4}\.md$")
STATUS_RE = re.compile(r"Статус:\*\*\s*(.+)")
CHAT_LINE = re.compile(r"^-\s*\[([^\]]+)\]\s*→\s*@([^\s:]+)\s*:\s*(.+)$")
# «беру TE-015», «начинаю работу над BE-014», «взял DO-009 в работу»
CLAIM = re.compile(
    r"(?:беру|берусь|начинаю|начал|взял|взялся|приступаю|делаю)\s+"
    r"(?:работу\s+)?(?:над\s+)?(?:задач[а-я]*\s+)?([A-Z]{2,3}-\d{2,4})",
    re.I,
)


def repo_root():
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True)
        if out.returncode == 0:
            return out.stdout.decode("utf-8", "ignore").strip()
    except Exception:
        pass
    return os.getcwd()


def load_config(path=None):
    path = path or os.path.join(repo_root(), CONFIG)
    if not os.path.exists(path):
        sys.exit("sweep: нет %s — это карта проекта (см. AGENTS.md §8)" % CONFIG)
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def pick(statuses, word, default):
    for st in statuses:
        if word in st.lower():
            return st
    return default


def normalize(raw, statuses):
    s = raw.strip().lower()
    closed = pick(statuses, "закрыт", statuses[-1])
    rejected = pick(statuses, "отклон", statuses[-1])
    review = pick(statuses, "проверк", closed)
    work = pick(statuses, "работе", closed)
    if "отклон" in s:
        return rejected, None
    for syn in ("выполн", "готов", "done", "сделана"):
        if syn in s:
            return closed, "выполнена"
    if "закрыт" in s:
        return closed, None
    if "проверк" in s:
        return review, None
    if "работе" in s:
        return work, None
    return statuses[0], None


def git_ts(path):
    """Время последнего коммита, затрагивающего файл (unix seconds) или None."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", path],
            capture_output=True, text=True, check=False,
        )
        v = out.stdout.strip()
        return int(v) if v.isdigit() else None
    except (OSError, ValueError):
        return None


def result_kind(text, status_raw):
    """'block' — есть содержательный ## Результат; 'inline' — только в строке статуса."""
    m = re.search(r"^##\s+Результат\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if m and len(m.group(1).strip()) >= 20:
        return "block"
    m = re.search(r"Результат:\*\*\s*(.+)", text)
    if m and len(m.group(1).strip()) >= 20:
        return "block"
    return "inline" if len(status_raw) >= 20 else "none"


def parse_chat_ts(raw, tz_offset_hours):
    """'2026-11-01 15:04' (местное время проекта) -> unix seconds UTC."""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            local = datetime.strptime(raw.strip(), fmt)
            return local.timestamp() - tz_offset_hours * 3600
        except ValueError:
            continue
    return None


def scan_tasks(cfg, args, now):
    statuses = cfg.get("statuses") or ["Открыта", "В работе", "На проверке", "Закрыта", "Отклонена"]
    backlog = cfg.get("backlog") or "5-backlog"
    findings = {"review": [], "work": [], "no_result": [], "vocab": []}
    tasks = {}

    folders = [f.get("folder") for f in (cfg.get("task_folders") or []) if f.get("folder")]
    if not folders:
        base = os.path.join(backlog)
        folders = [d for d in sorted(os.listdir(base))
                   if os.path.isdir(os.path.join(base, d))] if os.path.isdir(base) else []

    for role_dir in folders:
        base = os.path.join(backlog, role_dir)
        if not os.path.isdir(base):
            continue
        for fn in sorted(os.listdir(base)):
            if not TASK_ID.match(fn):
                continue
            path = os.path.join(base, fn).replace("\\", "/")
            with open(path, encoding="utf-8-sig") as f:
                text = f.read()
            m = STATUS_RE.search(text)
            raw = m.group(1).strip() if m else ""
            status, synonym = normalize(raw, statuses)
            ts = git_ts(path)
            age_h = round((now - ts) / 3600, 1) if ts else None
            item = {"id": fn[:-3], "role": role_dir, "path": path,
                    "status": status, "age_h": age_h}
            tasks[fn[:-3]] = item

            if not m:
                findings["vocab"].append({**item, "why": "нет поля Статус"})
                continue
            if synonym:
                findings["vocab"].append({**item, "why": "синоним вместо «%s»" % status})
            if status == pick(statuses, "проверк", statuses[-1]) and age_h is not None \
                    and age_h >= args.review_hours:
                findings["review"].append(item)
            if status == pick(statuses, "работе", statuses[-1]) and age_h is not None \
                    and age_h >= args.work_hours:
                findings["work"].append(item)
            if status == pick(statuses, "закрыт", statuses[-1]):
                kind = result_kind(text, raw)
                if kind != "block":
                    findings["no_result"].append(
                        {**item, "why": "только в строке статуса" if kind == "inline"
                         else "пояснения нет"})
    return findings, tasks, statuses


def scan_claims(cfg, args, tasks, statuses, now):
    """«Беру XX-001» в чате, а статус задачи всё ещё первый."""
    chat = cfg.get("chat") or os.path.join(cfg.get("backlog") or "5-backlog", "chat")
    tz = cfg.get("time_zone_offset", 0)
    first = statuses[0]
    claims = []
    if not os.path.isdir(chat):
        return claims
    for fn in sorted(os.listdir(chat)):
        if not fn.endswith(".md") or fn.startswith("_"):
            continue
        role = fn[:-3]
        path = os.path.join(chat, fn).replace("\\", "/")
        last = {}
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                m = CHAT_LINE.match(line.strip())
                if not m:
                    continue
                ts_raw, _to, text = m.group(1), m.group(2), m.group(3)
                for tid in CLAIM.findall(text):
                    last[tid.upper()] = (parse_chat_ts(ts_raw, tz), ts_raw, text[:120], role)
        for tid, (ts, ts_raw, text, role) in last.items():
            task = tasks.get(tid)
            if not task:
                claims.append({"id": tid, "role": role, "status": "нет задачи",
                               "age_h": None, "why": "в чате %s.md обещана несуществующая задача"
                               % role, "chat": path, "msg": text})
                continue
            if task["status"] != first:
                continue
            age_h = round((now - ts) / 3600, 1) if ts else None
            if age_h is None or age_h < args.claim_hours:
                continue
            claims.append({"id": tid, "role": task["role"], "status": task["status"],
                           "age_h": age_h, "why": "обещано в %s.md («%s…»), статус не менялся"
                           % (role, text[:60]), "chat": path, "msg": text})
    return claims


def scan_git_claims(cfg, args, tasks, statuses, now):
    """Коммит упоминает задачу, а статус не сдвинулся — работа идёт «мимо» поля Статус.

    Ловит случай, когда агент всё-таки сделал работу, но забыл перевести задачу
    в «В работе» (в XDiary так было на TE-015: коммит есть, статус «Открыта»).
    """
    first = statuses[0]
    hours = float((cfg.get("sweep") or {}).get("git_claim_hours", 24))
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - hours * 3600))
    try:
        out = subprocess.run(
            ["git", "log", "--since=" + since, "--format=%h%x1f%cs%x1f%s"],
            capture_output=True, text=True, check=False,
        )
        rows = out.stdout
    except OSError:
        return []

    claims = []
    seen = set()
    for line in rows.splitlines():
        parts = line.split("\x1f")
        if len(parts) < 3:
            continue
        sha, date, subject = parts[0], parts[1], parts[2]
        for tid in re.findall(r"\b([A-Z]{2,3}-\d{2,4})\b", subject):
            tid = tid.upper()
            task = tasks.get(tid)
            if not task or task["status"] != first or tid in seen:
                continue
            seen.add(tid)
            claims.append({"id": tid, "role": task["role"], "status": task["status"],
                           "age_h": None, "why": "коммит %s (%s) уже трогает задачу, "
                           "а статус не изменён" % (sha, date), "path": task["path"]})
    return claims


def render(findings, claims, args):
    total = sum(len(v) for v in findings.values()) + len(claims)
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    out = ["# Обход петли — %s" % stamp, ""]
    if not total:
        out += ["Требующих внимания задач нет. Роли могут спать.", ""]
        return "\n".join(out), 0

    def bucket(title, items, extra):
        out.append("## %s (%d)" % (title, len(items)))
        out.append("")
        for it in items:
            if it.get("age_h") is None:
                age = "файл ещё не закоммичен"
            else:
                age = "%.0f ч без движения" % it["age_h"]
            note = " — %s" % it["why"] if it.get("why") else ""
            out.append("- **%s** — %s, %s%s. %s" % (
                it["id"], it["status"], age, note, extra))
            ref = it.get("path") or it.get("chat") or ""
            out.append("  `%s`" % ref)
        out.append("")

    if claims:
        bucket("Обещано, но не начато", claims,
               "Первый шаг — `Статус: В работе` и строка в чат, иначе ход был пустой.")
    if findings["review"]:
        bucket("Ждут проверки дольше %d ч" % args.review_hours, findings["review"],
               "Проверить и написать владельцу «можно закрывать».")
    if findings["work"]:
        bucket("В работе дольше %d ч" % args.work_hours, findings["work"],
               "Спросить роль в чате: жив ли, что мешает.")
    if findings["no_result"]:
        bucket("Закрыты без блока «## Результат»", findings["no_result"],
               "Дописать 1–2 строки: что и где сделано.")
    if findings["vocab"]:
        bucket("Статус вне словаря", findings["vocab"],
               "Только значения из loop.config.json `statuses`.")

    out += ["---", "Всего находок: %d." % total, ""]
    return "\n".join(out), total


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Обход петли (задачи без движения + пустые обещания)")
    ap.add_argument("--config", default=None, help="путь к loop.config.json")
    ap.add_argument("--review-hours", type=float, default=None)
    ap.add_argument("--work-hours", type=float, default=None)
    ap.add_argument("--claim-hours", type=float, default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fail-on-stale", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    defaults = cfg.get("sweep") or {}
    if args.review_hours is None:
        args.review_hours = float(defaults.get("review_hours", 24))
    if args.work_hours is None:
        args.work_hours = float(defaults.get("work_hours", 72))
    if args.claim_hours is None:
        args.claim_hours = float(defaults.get("claim_hours", 0.5))

    now = time.time()
    findings, tasks, statuses = scan_tasks(cfg, args, now)
    claims = scan_claims(cfg, args, tasks, statuses, now)
    claims += scan_git_claims(cfg, args, tasks, statuses, now)
    text, total = render(findings, claims, args)

    if args.json:
        print(json.dumps({"total": total, "claims": claims, "findings": findings},
                         ensure_ascii=False, indent=2))
    else:
        print(text)

    return 1 if (args.fail_on_stale and total) else 0


if __name__ == "__main__":
    sys.exit(main())
