#!/usr/bin/env python3
# КОПИЯ из LoopMaker/health.py (источник правды там; правка — в обоих местах).
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
import glob
import json
import os
import re
import statistics
import subprocess
import sys
import collections
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


def run(root, args, timeout=30):
    """Запустить инструмент из tools/ и вернуть (rc, stdout).

    Таймаут (2026-11-12): часть гейтов (chat_lint --check, sweep) делают тяжёлые
    git-операции и вешали дашборд. При таймауте — честное «не измерено» (rc=2),
    дашборд не должен зависать из-за одного медленного инструмента.
    """
    cmd = [sys.executable] + args
    try:
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", check=False,
                           timeout=timeout)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        return 2, ""
    except OSError as e:
        return 1, str(e)


def sh(root, args, timeout=30):
    """Внешняя команда (git) в корне проекта: (rc, stdout)."""
    try:
        p = subprocess.run(args, cwd=root, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", check=False,
                           timeout=timeout)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        return 2, ""
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


AUTHOR_BY_FOLDER = {
    "arch": "ar",
    "devops": "do",
    "back-end": "be",
    "front-end": "fe",
    "tester": "te",
}


def metric_review_domain(root, cfg):
    """Сколько задач имеют ревьюера, отличного от автора (§2.1 п.6: саморевью запрещено).

    Автор определяется папкой задачи. Возвращает (ok, total, bad_ids).
    """
    backlog = cfg.get("backlog") or "5-backlog"
    ok = 0
    total = 0
    bad = []
    for folder, author in AUTHOR_BY_FOLDER.items():
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
            if got != author:
                ok += 1
            else:
                bad.append("%s:%s" % (fn[:-3], got))
    return ok, total, bad


def metric_cycle(root, cfg):
    """Lead time закрытых задач: медиана дней от первой версии файла до закрытия.

    Источник — git, а не поля задач. Оптимизация (2026-11-12): раньше для каждой
    закрытой карточки делались 2 `git log` (≈662 вызова ≈ 80 с) — дашборд вешался.
    Теперь два `git log --name-only` на весь бэклог, первый/последний коммит файла
    парсится из списка путей.
    """
    backlog = cfg.get("backlog") or "5-backlog"
    files = []
    for path in sorted(glob.glob(os.path.join(root, backlog, "*", "*.md"))):
        rel = os.path.relpath(path, root).replace("\\", "/")
        if "/chat/" in rel:
            continue
        with open(path, encoding="utf-8-sig") as f:
            head = f.read(2000)
        if "Закрыта" in head:
            files.append(rel)
    if not files:
        return None, 0, 0

    def first_touch(args):
        """{файл: unix-время} по первому появлению в `git log --name-only`."""
        out_map = {}
        rc, out = sh(root, ["git", "log", "--format=%at", "--name-only"] + args)
        if rc != 0:
            return out_map
        cur = None
        for raw in out.splitlines():
            s = raw.strip()
            if s.isdigit():
                cur = int(s)
            elif s and cur is not None:
                rel2 = s.replace("\\", "/")
                if rel2 not in out_map:
                    out_map[rel2] = cur
        return out_map

    created = first_touch(["--diff-filter=A", "--reverse", "--", backlog])
    closed = first_touch(["--", backlog])

    days, skipped = [], 0
    for rel in files:
        c1 = created.get(rel)
        c2 = closed.get(rel)
        if c1 is None or c2 is None:
            skipped += 1
            continue
        delta = (c2 - c1) / 86400.0
        if 0 <= delta < 400:
            days.append(delta)
        else:
            skipped += 1
    if not days:
        return None, 0, skipped
    return statistics.median(days), len(days), skipped


def metric_lm_sla(root, cfg):
    """SLA процессной роли: медиана минут от вопроса `→ @LM` до ответа в lm.md.

    Источник времени — **git committer-time** (стена часов), а не штамп в строке чата.
    Шкала чата канонически впереди машинных часов (сдвиг `chat_time.shift_hours`),
    поэтому разность штампов в «минутах чата» не равна минутам ответа: на 2026-11-09
    она показывала 4605 мин — артефакт, а не регресс (D-13: метрика называет источник).

    `git blame --line-porcelain` даёт committer-time коммита, который внёс каждую
    строку — это и есть настоящий момент записи. Нет git/блейма — честное n/a.
    """
    backlog = cfg.get("backlog") or "5-backlog"
    chat = os.path.join(root, backlog, "chat")
    if not os.path.isdir(chat):
        return None, 0
    Q = re.compile(r"→\s*@lm\b", re.I)
    R = re.compile(r"→\s*@(\w+)", re.I)

    def line_times(path, limit=500):
        """[(unixtime, text)] для последних `limit` строк файла; None, если блейм недоступен.

        Дефект (2026-11-12): полный `git blame --line-porcelain` на весь чат (6326 строк)
        стал квадратично медленным и вешал `health.py` (>2 мин). Блеймим только хвост
        (последние `limit` строк) через `-L` — для медианы SLA свежих ответов хватает.
        """
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                total = sum(1 for _ in f)
        except OSError:
            return None
        start = max(1, total - limit + 1)
        rc, out = sh(root, ["git", "blame", "--line-porcelain",
                            "-L", "%d,%d" % (start, total), "--", path])
        if rc != 0 or not out:
            return None
        times, cur = [], None
        for raw in out.splitlines():
            if raw.startswith("committer-time "):
                try:
                    cur = int(raw.split(None, 1)[1])
                except (IndexError, ValueError):
                    cur = None
            elif raw.startswith("\t"):
                times.append((cur, raw[1:]))
        return times

    answers = line_times(os.path.join(chat, "lm.md"))
    if not answers:
        return None, 0

    questions = []
    for name in sorted(os.listdir(chat)):
        if not name.endswith(".md") or name in ("README.md", "_dialogue.md", "lm.md", "_lint.md"):
            continue
        author = name[:-3].lower()
        times = line_times(os.path.join(chat, name))
        if not times:
            continue
        for t, text in times:
            if t is None or not Q.search(text):
                continue
            questions.append((t, author))

    pairs = []
    for tq, author in questions:
        best = None
        for tr, text in answers:
            if tr is None or tr < tq:
                continue
            m = R.search(text)
            if m and m.group(1).lower() == author:
                best = tr if best is None else min(best, tr)
        if best is not None:
            pairs.append((best - tq) / 60.0)
    if not pairs:
        return None, 0
    return statistics.median(pairs), len(pairs)


def metric_wip_share(root):
    """Доля wip-коммитов в последних 200: показывает трение в общем дереве."""
    _rc, out = sh(root, ["git", "log", "-200", "--pretty=%s"])
    if not out:
        return None, 0
    lines = [l for l in out.splitlines() if l.strip()]
    if not lines:
        return None, 0
    wip = sum(1 for l in lines if l.strip().lower().startswith("wip("))
    return round(100.0 * wip / len(lines)), len(lines)


def metric_chat_format(root):
    """Доля сообщений, написанных не каноном (ct: без скобок, `->`, без `@`).

    Доставку это не ломает (парсер толерантен), но ломает чужие регексы и глаза
    людей. Источник — строка отчёта `chat_lint --report`.
    """
    rc, out = run(root, ["tools/chat_lint.py", "--config", CONFIG, "--report"])
    m = re.search(r"формат: (\d+) из (\d+)", out or "")
    if not m:
        return 0, 0
    return int(m.group(1)), int(m.group(2))


def metric_ci_reach(root, cfg, enabled=True, limit=15):
    """Доля прогонов, дошедших до шагов (не вытесненных из очереди).

    Считается по API: берём последние N прогонов; для `cancelled` смотрим джобы —
    если у всех шагов ноль, прогон «пустой» (вытеснение), а не результат.
    Возвращает (reach_pct, total, void) или (None, 0, 0) при недоступности API.
    """
    if not enabled:
        return None, 0, 0
    repo = (cfg.get("health") or {}).get("repo") or "Ekstrem/XDiary"
    token = os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "Bearer %s" % token
    try:
        req = urllib.request.Request("%s/repos/%s/actions/runs?per_page=%d" % (API, repo, limit),
                                     headers=headers)
        data = json.load(urllib.request.urlopen(req, timeout=20))
    except Exception:
        return None, 0, 0
    runs = [r for r in data.get("workflow_runs", []) if r.get("status") == "completed"]
    if not runs:
        return None, 0, 0
    void = 0
    for r in runs:
        if r.get("conclusion") != "cancelled":
            continue
        try:
            req = urllib.request.Request("%s/repos/%s/actions/runs/%s/jobs" % (API, repo, r["id"]),
                                         headers=headers)
            jobs = json.load(urllib.request.urlopen(req, timeout=20)).get("jobs", [])
        except Exception:
            continue
        if sum(len(j.get("steps") or []) for j in jobs) == 0:
            void += 1
    total = len(runs)
    reach = round(100.0 * (total - void) / total)
    return reach, total, void


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



def norm_dwell_status(raw):
    """Канонизация статуса из git-диффа (синонимы → 8 статусов)."""
    s = re.split(r"[([\[]", raw.strip())[0].strip()
    for k, v in (("Открыта", "В работе"), ("Выполнена", "Закрыта"),
                 ("Готово", "Закрыта"), ("Done", "Закрыта"), ("Пауза", "В паузе")):
        if s.lower().startswith(k.lower()):
            return v
    for st in ("Идея", "На проработке", "ToDo", "В работе", "В паузе",
               "На проверке", "Закрыта", "Отклонена"):
        if s == st or s.startswith(st):
            return st
    return s.split()[0] if s else "?"


def _card_transitions(root, rel):
    """[(ts, status)] переходов статуса одной карточки, хронологически."""
    _rc, out = sh(root, ["git", "log", "-p", "-G", "Статус:", "--format=%ct", "--", rel])
    seq, cur = [], None
    for line in (out or "").splitlines():
        s = line.strip()
        if re.match(r"^\d{9,}$", s):
            cur = int(s)
        else:
            m = re.match(r"^\+\s*-?\s*\*\*Статус:?\*\*\s*(.+)", line)
            if m and cur is not None:
                st = norm_dwell_status(m.group(1))
                if not seq or seq[-1][1] != st:
                    seq.append((cur, st))
    seq.reverse()
    return seq


def metric_status_dwell(root, cfg):
    """Медиана дней в каждом статусе по git-переходам, с кешем по blob-sha.

    Полный пересчёт `git log -p -G` на каждую карточку ≈ минуты; кеш
    `health/dwell-cache.json` пересчитывает только изменившиеся карточки.
    Возвращает {статус: медиана_дней}.
    """
    backlog = cfg.get("backlog") or "5-backlog"
    cache_file = os.path.join(root, "health", "dwell-cache.json")
    cache = {}
    try:
        with open(cache_file, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}

    _rc, tree = sh(root, ["git", "ls-tree", "-r", "origin/main", "--", backlog])
    blob = {}
    for line in (tree or "").splitlines():
        p = line.split()
        if len(p) >= 4 and "/chat/" not in p[3] and re.search(r"[A-Z]{2}-\d+\.md$", p[3]):
            blob[p[3]] = p[2]

    dwell = collections.defaultdict(list)
    for rel, bsha in blob.items():
        ent = cache.get(rel)
        if isinstance(ent, dict) and ent.get("blob") == bsha:
            trans = ent.get("trans", [])
        else:
            trans = _card_transitions(root, rel)
            cache[rel] = {"blob": bsha, "trans": trans}
        for i in range(len(trans) - 1):
            t1, s1 = trans[i]
            t2, s2 = trans[i + 1]
            d = (t2 - t1) / 86400.0
            if 0 <= d < 400:
                dwell[s1].append(d)

    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except OSError:
        pass

    out = {}
    for s in ("В работе", "На проверке", "В паузе"):
        if dwell.get(s):
            out[s] = round(statistics.median(dwell[s]), 2)
    return out


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
    arch_dir = health.get("archive") or os.path.join("health", "archive")

    errors, warns = metric_lint(root)
    stale, _ = metric_stale(root)
    gates_ok, gates_total, gates_bad = metric_gates(root)
    p95 = metric_ci(root, cfg, jobs, enabled=not args.no_ci)
    # metric_ci_reach объявлена выше, но вызова не было -> NameError: name 'reach'
    # is not defined при рендере таблицы (health.yml). Вызов обязателен до строки,
    # где печатается «Прогоны, дошедшие до шагов».
    reach, reach_total, reach_void = metric_ci_reach(root, cfg, enabled=not args.no_ci)
    inbox = metric_inbox(root)
    skew, order = metric_chat_skew(root)
    rev_ok, rev_total, rev_bad = metric_review_domain(root, cfg)
    drift, drift_total = metric_drift(root, cfg)
    cycle_med, cycle_n, cycle_missing = metric_cycle(root, cfg)
    lm_med, lm_n = metric_lm_sla(root, cfg)
    wip_share, wip_n = metric_wip_share(root)
    fmt_tol, fmt_total = metric_chat_format(root)
    dwell = metric_status_dwell(root, cfg)

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
        "cycle_days": None if cycle_med is None else round(cycle_med, 1),
        "cycle_n": cycle_n,
        "lm_sla_min": None if lm_med is None else round(lm_med),
        "lm_sla_n": lm_n,
        "wip_share": wip_share,
        "chat_format_tolerant": fmt_tol,
        "chat_format_total": fmt_total,
        "drift_total": drift_total,
        "dwell_work": dwell.get("В работе"),
        "dwell_review": dwell.get("На проверке"),
        "dwell_pause": dwell.get("В паузе"),
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
        "| Прогоны, дошедшие до шагов | %s | >= 90%% | %s |" % (
            "нет данных" if reach is None else "%d%% из %d (пустых %d)" % (reach, reach_total, reach_void),
            "⚪ нет данных (нужен PAT @DO)" if reach is None else ("🟢" if reach >= 90 else "🟡")),
        "| CI p95 (худший джоб) | %s | < 300 с | %s |" % (
            "n/a" if p95 is None else "%d с" % p95, "🟢" if (p95 is None or p95 < 300) else "🔴"),
        "| Вопросы в чате без ответа | %s | 0 | %s |" % (
            inbox, "🟢" if not inbox else "🟡"),
        "| Метки чата вне шкалы / порядок | %s | 0 / 0 | %s |" % (
            "n/a" if skew is None else "%s / %s" % (skew, order),
            "🟢" if not (skew or order) else "🟡"),
        "| Ревью не автором (§2.1 п.6) | %s | 100%% | %s |" % (
            "n/a" if not rev_total else "%d/%d" % (rev_ok, rev_total),
            "🟢" if not rev_bad else "🟡"),
        "| Цикл задачи, медиана (дней) | %s | < 7 | %s |" % (
            "n/a" if cycle_med is None else "%.1f (n=%d%s)" % (
                cycle_med, cycle_n, "" if not cycle_missing else ", без дат %d" % cycle_missing),
            "🟢" if (cycle_med is not None and cycle_med < 7) else "🟡"),
        "| Время в статусах, медиана (дн) | %s | ревью < 1 | %s |" % (
            "n/a" if not dwell else "работа %s · ревью %s · пауза %s" % (
                dwell.get("В работе", "—"), dwell.get("На проверке", "—"), dwell.get("В паузе", "—")),
            "🟡" if dwell.get("На проверке", 0) and dwell["На проверке"] >= 1 else "🟢"),
        "| Ответ @LM, медиана (мин) | %s | < 30 | %s |" % (
            "нет данных" if lm_med is None else "%.0f (n=%d)" % (lm_med, lm_n),
            "⚪ нет данных" if lm_med is None else ("🟢" if lm_med < 30 else "🟡")),
        "| Формат чата не каноном | %s | < 10%% | %s |" % (
            "n/a" if not fmt_total else "%d из %d" % (fmt_tol, fmt_total),
            "🟢" if (not fmt_total or 100.0 * fmt_tol / fmt_total < 10) else "🟡"),
        "| Доля wip-коммитов | %s | < 20%% | %s |" % (
            "n/a" if wip_share is None else "%d%% из %d" % (wip_share, wip_n),
            "🟢" if (wip_share is None or wip_share < 20) else "🟡"),
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
        # Архив снимка на конец дня/итерации: markdown не затирается, историю видно глазами.
        # Сырые числа — в history.jsonl, человекочитаемый снимок — в health/archive/HEALTH-<дата>.md.
        arch_name = "HEALTH-%s.md" % now.strftime("%Y-%m-%d")
        os.makedirs(os.path.join(root, arch_dir), exist_ok=True)
        with open(os.path.join(root, arch_dir, arch_name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print("записано: %s, %s, %s" % (out_file, hist_file, os.path.join(arch_dir, arch_name)),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
