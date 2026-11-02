#!/usr/bin/env python3
"""CI-бюджет: сколько реально идут тяжёлые джобы (p50/p95) — цифра вместо «долго».

Зачем: в петле с агентами прогоны отменяют «потому что долго», а на деле `test`
проходит за минуту. Правило «не отменяй раньше 5 минут» (AGENTS §14) работает только
если есть чем назвать цифру. Этот скрипт считает её по завершённым успешным прогонам
и пишет markdown-таблицу.

    python tools/ci_metrics.py                        # repo из git remote, таблица в stdout
    python tools/ci_metrics.py --out ci-budget.md     # ещё и в файл
    python tools/ci_metrics.py --jobs test,front-e2e --limit 30
    python tools/ci_metrics.py --repo owner/name

Без токена читаются только **публичные** репозитории (GitHub API). Приватный репозиторий:
добавьте `--token` или переменную `GITHUB_TOKEN`.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import urllib.request

API = "https://api.github.com"


def repo_from_git():
    try:
        out = subprocess.run(["git", "remote", "get-url", "origin"],
                             capture_output=True, text=True, check=False)
        url = out.stdout.strip()
    except OSError:
        return None
    if not url:
        return None
    # git@github.com:owner/name.git  |  https://github.com/owner/name.git
    tail = url.split("github.com")[-1].lstrip(":/-")
    tail = tail[:-4] if tail.endswith(".git") else tail
    return tail if "/" in tail else None


def get(path, token=None):
    req = urllib.request.Request(API + path)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "loopmaker-ci-metrics")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print("  ! %s: %s" % (path, e), file=sys.stderr)
        return None


def duration_sec(job):
    from datetime import datetime
    def parse(s):
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    try:
        return (parse(job["completed_at"]) - parse(job["started_at"])).total_seconds()
    except Exception:
        return None


def pct(values, p):
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1)))))
    return ordered[k]


def main():
    ap = argparse.ArgumentParser(description="p50/p95 длительности джобов CI")
    ap.add_argument("--repo", default=None, help="owner/name (по умолчанию — из git remote)")
    ap.add_argument("--jobs", default="test,front-e2e", help="имена джобов через запятую")
    ap.add_argument("--limit", type=int, default=20, help="сколько последних прогонов смотреть")
    ap.add_argument("--workflow-path", default=None,
                    help="путь workflow, например .github/workflows/ci-tests.yml "
                         "(иначе берутся последние прогоны ВСЕХ workflow)")
    ap.add_argument("--out", default=None, help="куда записать markdown")
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    args = ap.parse_args()

    repo = args.repo or repo_from_git()
    if not repo:
        sys.exit("ci_metrics: не понял репозиторий — укажите --repo owner/name")

    if args.workflow_path:
        # GitHub ждёт имя файла или id, а не путь: «ci-tests.yml», не «.github/workflows/ci-tests.yml»
        wf = os.path.basename(args.workflow_path.strip().replace("\\", "/"))
        runs_url = "/repos/%s/actions/workflows/%s/runs?status=success&per_page=%d" % (
            repo, wf, max(args.limit * 3, 30))
    else:
        runs_url = "/repos/%s/actions/runs?status=success&per_page=%d" % (
            repo, max(args.limit * 3, 30))
    runs = get(runs_url, args.token)
    if not runs:
        sys.exit("ci_metrics: не удалось прочитать прогоны (приватный репозиторий? нужен токен)")

    wanted = [j.strip() for j in args.jobs.split(",") if j.strip()]
    samples = {j: [] for j in wanted}
    used = 0
    for run in runs.get("workflow_runs", []):
        if used >= args.limit:
            break
        detail = get("/repos/%s/actions/runs/%d/jobs" % (repo, run["id"]), args.token)
        if not detail:
            continue
        touched = False
        for job in detail.get("jobs", []):
            name = job.get("name")
            if name in samples and job.get("conclusion") == "success":
                d = duration_sec(job)
                if d is not None:
                    samples[name].append(d)
                    touched = True
        if touched:
            used += 1

    lines = ["# CI-бюджет: %s" % repo, "",
             "Прогонов просмотрено: %d (успешные). «Отменённый прогон — не доказательство» "
             "(AGENTS §14)." % used, "",
             "| Джоб | Замеров | p50 | p95 | max |", "|---|---|---|---|---|"]
    for job in wanted:
        vals = samples[job]
        if not vals:
            lines.append("| %s | 0 | — | — | — |" % job)
            continue
        lines.append("| %s | %d | %.0f с | %.0f с | %.0f с |" % (
            job, len(vals), pct(vals, 50), pct(vals, 95), max(vals)))
    lines += ["", "_Порог из AGENTS §14: прогон младше 5 минут не отменяет никто. "
                  "Если p95 ниже 5 минут — отмена почти всегда ошибка._", ""]
    text = "\n".join(lines)

    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print("записано: %s" % args.out, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
