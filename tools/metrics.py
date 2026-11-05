"""Коллектор метрик петли: projects.json -> тулы -> SCOREBOARD.md."""
import json, os, subprocess, sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Реестр: роль -> список (имя метрики, путь скрипта). Заполняется по мере реализации тулов.
METRICS = {
    "AR": [("adr_frozen", "tools/metrics/check_adr_frozen.py")],
    "BE": [("dtf_usage", "tools/metrics/check_dtf_usage.py")],
    "FE": [("fe_version", "tools/metrics/check_fe_version.py")],
}


def load_project():
    cfg = json.load(open(os.path.join(ROOT, "projects.json"), encoding="utf-8"))
    return cfg["projects"][cfg["current"]]


def run_tool(workbench, script):
    r = subprocess.run([sys.executable, os.path.join(ROOT, script), "--workbench", workbench],
                       capture_output=True, text=True, encoding="utf-8")
    out = (r.stdout or "").strip().splitlines()
    if out:
        try:
            return json.loads(out[-1])
        except Exception:
            pass
    return {"role": "?", "metric": script, "status": "unknown", "value": "", "detail": "no-json"}


def main():
    proj = load_project()
    wb = proj["workbench"]
    rows = []
    for role, tools in METRICS.items():
        for metric, script in tools:
            res = run_tool(wb, script)
            rows.append((role, metric, res.get("status"), res.get("value"), res.get("detail")))
    lines = ["# SCOREBOARD", "",
             f"> проект: {proj['name']} ({wb})",
             f"> собрано: {datetime.now(timezone.utc).isoformat()}",
             "",
             "| Роль | Метрика | Статус | Значение | Деталь |",
             "|---|---|---|---|---|"]
    for role, metric, status, value, detail in rows:
        lines.append(f"| {role} | {metric} | {status} | {value} | {detail} |")
    open(os.path.join(ROOT, "SCOREBOARD.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"SCOREBOARD.md обновлён: {len(rows)} метрик")


if __name__ == "__main__":
    main()
