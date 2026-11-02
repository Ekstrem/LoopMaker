#!/usr/bin/env python3
"""Дашборд «здоровье петли»: пять чисел, которые видно без чтения чата.

Идея: не заводить новые метрики, а собрать те, что уже считаются гейтами, в одну
таблицу со светофором и трендом. Дашборд ничего не лечит — он только делает
состояние петли наблюдаемым (и превращает «кажется, всё нормально» в цифру).

Метрики:
  1. ошибки/предупреждения линтера статусов (`tools/status_lint.py`);
  2. задачи без движения и «обещано, но не начато» (`tools/sweep.py`);
  3. гигиена гейтов: `stats --check`, `chat_lint --check`, `check_workflow_paths`;
  4. CI: худший p95 по тяжёлым джобам (`tools/ci_metrics.py`, нужна сеть);
  5. вопросы в чате без ответа (`tools/chat_inbox.py`).
Дополнительно (если рядом лежит LoopMaker): сколько копий инструментов разошлось.

Использование:
    python tools/health.py                     # таблица в stdout
    python tools/health.py --write             # + HEALTH.md и history.jsonl
    python tools/health.py --no-ci             # без сетевых запросов (быстро)
    python tools/health.py --json

Код возврата: 0 всегда (дашборд не гейт), кроме ошибки конфига.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

API = "https://api.github.com"
CONFIG = "loop.config.json"
TASK_ID = re.compile(r"^(?:[A-Z]{2,3})-\d{2,4}\.md$")
DEFAULT_JOBS = ["test", "front-e2e"]
SPARK = "▁▂▃▄▅▆▇█"


def repo_root(arg=None):
    if arg:
        return os.path.abspath(arg)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config(root):
    path = os.path.join(root, CONFIG)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def run(root, args):
    """Запустить инструмент из tools/ и вернуть (rc, stdout)."""
    cmd = [sys.executable] + args
    try:
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", check=False)
        return p.returncode, p.stdout
    except OSError as e:
        return 1, str(e)


def metric_lint(root):
    rc, out = run(root, ["tools/status_lint.py", "--config", CONFIG, "--json"])
    try:
        d = json.loads(out)
        return d.get("errors", 0), d.get("warnings", 0)
    except Exception:
        return None, None


def metric_stale(root):
    rc, out = run(root, ["tools/sweep.py", "--config", CONFIG, "--json"])
    try:
        d = json.loads(out)
        return d.get("total", 0), rc
    except Exception:
        return None, rc


def metric_gates(root):
    """Три дешёвых гейта: README свежий, формат чата, фильтры путей у workflow."""
    checks = []
    for name, args in (
        ("stats", ["tools/stats.py", "--config", CONFIG, "--check"]),
        ("chat", ["tools/chat_lint.py", "--config", CONFIG, "--check"]),
        ("wf", ["tools/check_workflow_paths.py"]),
    ):
        rc, _ = run(root, args)
        checks.append((name, rc == 0))
    ok = sum(1 for _, good in checks if good)
    return ok, len(checks), [n for n, good in checks if not good]


def _api(path, token=None):
    import urllib.request
    req = urllib.request.Request(API + path)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "loopmaker-health")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _dur(job):
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    try:
        a = datetime.strptime(job["started_at"], fmt)
        b = datetime.strptime(job["completed_at"], fmt)
        return (b - a).total_seconds()
    except Exception:
        return None


def metric_ci(root, cfg, jobs, enabled=True):
    """Худший p95 по тяжёлым джобам. Считаем напрямую через GitHub API:
    так дашборд не зависит от того, как назван файл метрик в проекте."""
    if not enabled:
        return None
    h = cfg.get("health") or {}
    repo = h.get("repo") or _repo_from_git(root)
    if not repo:
        return None
    wf = os.path.basename((h.get("workflow") or "ci-tests.yml").replace("\\", "/"))
    token = os.environ.get("GITHUB_TOKEN")
    try:
        runs = _api("/repos/%s/actions/workflows/%s/runs?status=success&per_page=20"
                    % (repo, wf), token)
    except Exception:
        return None
    wanted = set(jobs)
    samples = {j: [] for j in jobs}
    used = 0
    for run in runs.get("workflow_runs", []):
        if used >= 10:
            break
        try:
            detail = _api("/repos/%s/actions/runs/%d/jobs" % (repo, run["id"]), token)
        except Exception:
            continue
        touched = False
        for job in detail.get("jobs", []):
            if job.get("name") in wanted and job.get("conclusion") == "success":
                d = _dur(job)
                if d is not None:
                    samples[job["name"]].append(d)
                    touched = True
        if touched:
            used += 1
    p95s = []
    for vals in samples.values():
        if vals:
            vals.sort()
            p95s.append(vals[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))])
    return round(max(p95s)) if p95s else None


def _repo_from_git(root):
    try:
        out = subprocess.run(["git", "remote", "get-url", "origin"], cwd=root,
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", check=False)
    except OSError:
        return None
    url = (out.stdout or "").strip()
    if "github.com" not in url:
        return None
    tail = url.split("github.com")[-1].lstrip(":/-")
    tail = tail[:-4] if tail.endswith(".git") else tail
    return tail if "/" in tail else None



def metric_chat_skew(root):
    """Метки чата вне шкалы (только «новая шкала» — что после строки-маркера).

    Источник — chat_lint --report: строка «метки времени: впереди шкалы N ... порядок нарушен M».
    Возвращает (skew, order) или (None, None), если линтер ничего не сказал.
    """
    rc, out = run(root, ["tools/chat_lint.py", "--config", CONFIG, "--report"])
    m = re.search(r"впереди шкалы (\d+).*?порядок нарушен (\d+)", out or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    if "метки времени" not in (out or ""):
        return 0, 0
    return None, None


REVIEW_BY_FOLDER = {
    "arch": "ar",
    "devops": "do",
    "back-end": "tl",
    "front-end": "tl",
    "tester": "tl",
}


def metric_review_domain(root, cfg):
    """Доля задач на проверке/in-work, где `Проверяет:` совпадает с доменом папки.

    Правило §2.1: ревьюер определяется папкой задачи; саморевью (автор = ревьюер)
    уходит координатору. Возвращает (ok, total, bad_ids).
    """
    backlog = cfg.get("backlog") or "5-backlog"
    ok = 0
    total = 0
    bad = []
    for folder, expected in REVIEW_BY_FOLDER.items():
        base = os.path.join(root, backlog, folder)
        if not os.path.isdir(base):
            continue
        for fn in sorted(os.listdir(base)):
            if not fn.endswith(".md") or not TASK_ID.match(fn):
                continue
            path = os.path.join(base, fn)
            with open(path, encoding="utf-8-sig") as f:
                text = f.read()
            m = re.search(r"Проверяет:\*\*\s*(@?\w+)", text)
            if not m:
                continue
            total += 1
            got = m.group(1).lower().lstrip("@")
            if got == expected:
                ok += 1
            else:
                bad.append("%s:%s" % (fn[:-3], got))
    return ok, total, bad


def metric_inbox(root):
    rc, out = run(root, ["tools/chat_inbox.py"])
    m = re.search(r"(\d+)", out or "")
    if rc == 0 and (not m or m.group(1) == "0"):
        return 0
    return int(m.group(1)) if m else (0 if rc == 0 else 1)


def metric_drift(root, cfg):
    """Сколько копий инструментов разошлось с LoopMaker (если он рядом)."""
    lm = os.environ.get("LOOPMAKER_DIR") or os.path.join(os.path.dirname(root), "LoopMaker")
    if not os.path.isdir(os.path.join(lm, "tools")):
        return None, None
    drift, total = 0, 0
    for name in sorted(os.listdir(os.path.join(root, "tools"))):
        if not name.endswith(".py"):
            continue
        mine = os.path.join(root, "tools", name)
        theirs = os.path.join(lm, "tools", name)
        if not os.path.exists(theirs):
            continue
        total += 1
        try:
            a = open(mine, encoding="utf-8", errors="ignore").read()
            b = open(theirs, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        # копия помечена шапкой; сравниваем остальное тело
        a = a.lstrip("\ufeff")
        a_body = re.sub(r"# КОПИЯ из LoopMaker[^\n]*\n", "", a, count=1)
        norm = lambda s: s.replace("\r\n", "\n").strip()
        if norm(a_body) != norm(b):
            drift += 1
    return drift, total


def verdict(errors, stale, gates_bad, p95, inbox):
    red, yellow = [], []
    if errors:
        red.append("ошибки линтера")
    if gates_bad:
        red.append("гейт: " + ", ".join(gates_bad))
    if p95 and p95 >= 300:
        red.append("CI p95 ≥ 5 мин")
    if stale:
        yellow.append("задачи без движения")
    if inbox:
        yellow.append("вопросы без ответа")
    if red:
        return "🔴", red + yellow
    if yellow:
        return "🟡", yellow
    return "🟢", []


def spark(values):
    vals = [v for v in values if isinstance(v, (int, float))]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return SPARK[3] * len(vals)
    return "".join(SPARK[min(7, int((v - lo) / (hi - lo) * 7))] for v in vals)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Дашборд здоровья петли")
    ap.add_argument("--root", default=None)
    ap.add_argument("--write", action="store_true", help="обновить HEALTH.md и историю")
    ap.add_argument("--no-ci", action="store_true", help="не ходить в GitHub API")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = repo_root(args.root)
    cfg = load_config(root)
    health = cfg.get("health") or {}
    jobs = health.get("jobs") or DEFAULT_JOBS
    out_file = health.get("file") or "HEALTH.md"
    hist_file = health.get("history") or os.path.join("health", "history.jsonl")
    keep = int(health.get("keep") or 30)

    errors, warns = metric_lint(root)
    stale, _ = metric_stale(root)
    gates_ok, gates_total, gates_bad = metric_gates(root)
    p95 = metric_ci(root, cfg, jobs, enabled=not args.no_ci)
    inbox = metric_inbox(root)
    skew, order = metric_chat_skew(root)
    rev_ok, rev_total, rev_bad = metric_review_domain(root, cfg)
    drift, drift_total = metric_drift(root, cfg)

    now = datetime.now(timezone.utc)
    row = {
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lint_errors": errors,
        "lint_warnings": warns,
        "stale": stale,
        "gates_ok": gates_ok,
        "gates_total": gates_total,
        "ci_p95": p95,
        "inbox": inbox,
        "chat_skew": skew,
        "chat_order": order,
        "review_ok": rev_ok,
        "review_total": rev_total,
        "drift": drift,
        "drift_total": drift_total,
    }

    mark, reasons = verdict(errors or 0, stale or 0, gates_bad, p95, inbox)

    if args.json:
        print(json.dumps({"verdict": mark, "reasons": reasons, "metrics": row},
                         ensure_ascii=False, indent=2))
        return 0

    def cell(ok):
        return "🟢" if ok else "🔴"

    lines = [
        "# Здоровье петли — %s" % now.strftime("%Y-%m-%d %H:%M UTC"),
        "",
        "**Вердикт: %s%s**" % (mark, (" — " + ", ".join(reasons)) if reasons else " — всё в норме"),
        "",
        "| Метрика | Значение | Цель | |",
        "|---|---|---|---|",
        "| Ошибки статусов (линтер) | %s | 0 | %s |" % (
            "n/a" if errors is None else errors, cell(not errors)),
        "| Предупреждения статусов | %s | 0 (после бэкфилла) | %s |" % (
            "n/a" if warns is None else warns, "🟡" if warns else "🟢"),
        "| Задачи без движения | %s | 0 | %s |" % (
            "n/a" if stale is None else stale, "🟡" if stale else "🟢"),
        "| Гейты (stats/chat/wf) | %d/%d | %d/%d | %s |" % (
            gates_ok, gates_total, gates_total, gates_total,
            cell(gates_ok == gates_total)),
        "| CI p95 (худший джоб) | %s | < 300 с | %s |" % (
            "n/a" if p95 is None else "%d с" % p95, "🟢" if (p95 is None or p95 < 300) else "🔴"),
        "| Вопросы в чате без ответа | %s | 0 | %s |" % (
            inbox, "🟢" if not inbox else "🟡"),
        "| Метки чата вне шкалы / порядок | %s | 0 / 0 | %s |" % (
            "n/a" if skew is None else "%s / %s" % (skew, order),
            "🟢" if not (skew or order) else "🟡"),
        "| Ревью по домену (§2.1) | %s | 100%% | %s |" % (
            "n/a" if not rev_total else "%d/%d" % (rev_ok, rev_total),
            "🟢" if not rev_bad else "🟡"),
        "| Копии инструментов разошлись | %s | 0 | %s |" % (
            "n/a" if drift is None else "%d/%d" % (drift, drift_total),
            "n/a" if drift is None else ("🟢" if drift == 0 else "🟡")),
        "",
        "> Считается из тех же гейтов, что и CI: `status_lint`, `sweep`, `stats --check`,",
        "> `chat_lint --check`, `check_workflow_paths`, `ci_metrics`. Дашборд ничего не лечит —",
        "> он делает состояние наблюдаемым (AGENTS §16).",
        "",
    ]

    # история: читаем, добавляем, рисуем тренды
    hist_path = os.path.join(root, hist_file)
    history = []
    if os.path.exists(hist_path):
        with open(hist_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    history.append(json.loads(line))
                except ValueError:
                    continue
    tail = history[-(keep - 1):] + [row] if keep > 1 else [row]

    if len(tail) > 1:
        lines += ["## Тренд (последние %d)" % len(tail), "",
                  "- ошибки статусов: `%s`" % spark([r.get("lint_errors") for r in tail]),
                  "- задачи без движения: `%s`" % spark([r.get("stale") for r in tail]),
                  "- CI p95: `%s`" % spark([r.get("ci_p95") for r in tail]),
                  "- гейты ok: `%s`" % spark([r.get("gates_ok") for r in tail]),
                  ""]

    lines += ["## Последние замеры", "", "| Дата (UTC) | Ошибки | Предупр. | Без движения | Гейты | CI p95 | Вопросы |",
              "|---|---|---|---|---|---|---|"]
    for r in tail[-10:]:
        lines.append("| %s | %s | %s | %s | %s/%s | %s | %s |" % (
            (r.get("ts") or "")[:16].replace("T", " "),
            r.get("lint_errors"), r.get("lint_warnings"), r.get("stale"),
            r.get("gates_ok"), r.get("gates_total"),
            r.get("ci_p95") or "n/a", r.get("inbox")))
    lines.append("")

    text = "\n".join(lines)
    print(text)

    if args.write:
        with open(os.path.join(root, out_file), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.makedirs(os.path.dirname(hist_path) or ".", exist_ok=True)
        with open(hist_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print("записано: %s, %s" % (out_file, hist_file), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
