#!/usr/bin/env python3
"""commit_whip: кнут за нарушения комита (PDR-045).

Находит коммиты, где роль вышла за свою зону (коммит чужих путей / `git commit` без
`-- <paths>`), и печатает машиночитаемые кнут-строки. Доставку делает субагент (не плагин):
он читает эти строки и пишет в outbox нарушителя `→ @роль: кнут — <факт> — <ссылка>`.

    python tools/commit_whip.py --range origin/main~20..origin/main
    python tools/commit_whip.py --range origin/main~20..origin/main --json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_paths as cp

RULES = {
    "zone": ("§1/§5: коммит только своими путями", "AGENTS.md#5-git-и-проверки"),
}


def marker_files(sha):
    """Файлы коммита, содержащие маркеры конфликта (в версии sha)."""
    out = []
    names = cp.git("show", "--name-only", "--pretty=format:", sha).split()
    for f in names:
        if not f.endswith(".md"):
            continue
        try:
            body = cp.git("show", "%s:%s" % (sha, f))
            if any(l.startswith(("<<<<<<<", "=======", ">>>>>>>")) for l in body.splitlines()):
                out.append(f)
        except SystemExit:
            continue
    return out


def violations(rng, cfg):
    res = []
    for sha, subject, paths in cp.read_commits(rng):
        role = cp.detect_role(cfg, subject)
        if not role:
            continue
        allowed = cp.zones_for(cfg, role, False)
        if allowed is None:
            continue
        bad = [p for p in paths if not cp.matches(p, allowed)]
        if bad:
            res.append({"role": role, "sha": sha, "rule": "zone", "files": bad, "subject": subject})
        for f in marker_files(sha):
            if f in paths or True:
                res.append({"role": role, "sha": sha, "rule": "markers", "files": [f], "subject": subject})
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--range", default="origin/main~20..origin/main")
    ap.add_argument("--config", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = cp.load_config(a.config)
    v = violations(a.range, cfg)
    if a.json:
        print(json.dumps(v, ensure_ascii=False, indent=1))
        return 0
    if not v:
        print("commit_whip: нарушений комита нет")
        return 0
    for x in v:
        name, link = RULES[x["rule"]]
        files = ", ".join(x["files"][:4])
        print("→ @%s: кнут — коммит %s нарушил %s: %s — см. %s. Поправь, чужое верни."
              % (x["role"], x["sha"][:8], name, files, link))
    return len(v)


if __name__ == "__main__":
    sys.exit(main())
