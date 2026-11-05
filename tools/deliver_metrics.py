"""Доставка кнута/пряника: SCOREBOARD.md -> текст в outbox роли (локально)."""
import json, os, re, subprocess, sys, argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OUTBOX = {"AR":"ar.md","BE":"be.md","FE":"fe.md","DO":"do.md","TE":"te.md","TL":"tl.md","PO":"po.md","LM":"lm.md"}
RULE = {
    "adr_frozen": "Roles/Architect/RULES.Architect.md §Процесс ADR (старый ADR — только статус)",
    "dtf_usage": "Roles/BackEnd/RULES.BackEnd.md §Дисциплина EDM/DF (нет своих, DTF обязателен)",
    "fe_version": "Roles/FrontEnd/RULES.FrontEnd.md §Версия (APP_VERSION + CACHE парой)",
}


def parse_scoreboard():
    rows = []
    for ln in open(os.path.join(ROOT, "SCOREBOARD.md"), encoding="utf-8").read().splitlines():
        m = re.match(r"\| (\w+) \| (\w+) \| (\w+) \| (.+?) \| (.*?) \|", ln)
        if m and m.group(1) != "Роль":
            rows.append((m.group(1), m.group(2), m.group(3), m.group(4).strip(), m.group(5).strip()))
    return rows


def project_wb():
    cfg = json.load(open(os.path.join(ROOT, "projects.json"), encoding="utf-8"))
    wb = cfg["projects"][cfg["current"]]["workbench"]
    return os.path.normpath(os.path.join(ROOT, wb)) if not os.path.isabs(wb) else wb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="только показать тексты, не писать")
    args = ap.parse_args()
    wb = project_wb()
    lines = []
    for role, metric, status, value, detail in parse_scoreboard():
        if role not in OUTBOX:
            continue
        if status == "violation":
            text = f"\u2192 @{role}: метрика {metric}: {value} ({detail}) — перечитай {RULE.get(metric, 'правила')}; отвечать не нужно."
        elif status == "ok":
            text = f"\u2192 @{role}: метрика {metric}: {value} — так держать."
        else:
            continue
        lines.append((role, text))
    if args.dry_run:
        for role, text in lines:
            print(f"[dry-run] {OUTBOX[role]}: {text}")
        print(f"[dry-run] всего {len(lines)} текстов (не записано)")
        return 0
    for role, text in lines:
        outbox = os.path.join(wb, "5-tech-backlog", "chat", OUTBOX[role])
        stamp = subprocess.run([sys.executable, os.path.join(wb, "tools", "chat_lint.py"),
                                "--config", os.path.join(wb, "loop.config.json"), "--next-stamp"],
                               capture_output=True, text=True, encoding="utf-8").stdout.strip()
        with open(outbox, "a", encoding="utf-8") as f:
            f.write(f"- [{stamp}] {text}\n")
    print(f"доставлено {len(lines)} текстов в outbox ролей")
    return 0


if __name__ == "__main__":
    sys.exit(main())
