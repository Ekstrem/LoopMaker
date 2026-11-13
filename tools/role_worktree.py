#!/usr/bin/env python
"""Worktree isolation for roles (owner 2026-11-13, reversible).

Each role gets its OWN worktree directory (../XDiary-<role>) so roles never
switch branches inside the shared repo (no checkout races). The shared repo
XDiary stays on main for reading origin / board / summary only.

Usage:
    python tools/role_worktree.py --role XX      # create or refresh role worktree
    python tools/role_worktree.py --list         # list worktrees
    python tools/role_worktree.py --prune        # drop stale worktree metadata
"""
import argparse
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PARENT = os.path.dirname(REPO)


def git(*a, cwd=REPO, check=True):
    r = subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        sys.stderr.write((r.stderr or r.stdout).strip() + "\n")
        sys.exit(r.returncode)
    return r


def branch_exists(ref):
    return git("rev-parse", "--verify", ref, check=False).returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--prune", action="store_true")
    a = ap.parse_args()

    git("fetch", "origin", "--prune", check=False)

    if a.prune:
        git("worktree", "prune")
        print("stale worktree metadata pruned")
        return

    if a.list:
        out = git("worktree", "list", "--porcelain", check=False).stdout
        for line in out.splitlines():
            if line.startswith("worktree "):
                print(line[9:])
        return

    if not a.role:
        ap.error("--role XX | --list | --prune")

    role = a.role.lower().strip()
    branch = f"role/{role}"
    wt = os.path.join(PARENT, f"XDiary-{role}")

    # Ensure a local branch exists (track origin role/<role>, else main).
    if not branch_exists(branch):
        if branch_exists(f"origin/{branch}"):
            git("branch", "--track", branch, f"origin/{branch}")
        else:
            git("branch", branch, "origin/main")

    if os.path.isdir(wt):
        r = git("merge", "--ff-only", f"origin/{branch}", cwd=wt, check=False)
        print(f"worktree обновлён: {wt} на {branch}")
        if r.returncode != 0:
            print("  (ff-only не удался — в дереве есть свои коммиты/правки, оставляю как есть)")
    else:
        git("worktree", "add", wt, branch)
        print(f"worktree создан: {wt} на {branch}")

    git("fetch", "origin", branch, check=False)


if __name__ == "__main__":
    main()
