"""Push local commits to GitHub via REST API (git smart-HTTP blocked, api.github.com OK).

Usage: python push_via_api.py <commit_range_base>  # pushes commits base..HEAD in order
Reconstructs each commit with blobs -> tree -> commit -> ref update, preserving
per-commit message and file versions exactly as in local git history.
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import urllib.request

OWNER, REPO = "jingjiangze", "autotask-platform"
BRANCH = "stage-cloud"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"


def api(method: str, path: str, token: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{API}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "User-Agent": "autotask-push"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"API {method} {path} -> {e.code}: {e.read().decode()[:300]}")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True,
                          encoding="utf-8").stdout.strip()


def main() -> None:
    token = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                           capture_output=True, text=True).stdout
    token = token.split("password=", 1)[1].splitlines()[0]
    base = sys.argv[1]

    commits = git("log", f"{base}..HEAD", "--format=%H").splitlines()[::-1]  # oldest first
    print(f"pushing {len(commits)} commit(s): {base}..HEAD")

    # remote 当前 tip（父提交）
    ref = api("GET", f"/git/ref/heads/{BRANCH}", token)
    parent = ref["object"]["sha"]
    parent_commit = api("GET", f"/git/commits/{parent}", token)
    parent_tree = parent_commit["tree"]["sha"]

    for sha in commits:
        msg = git("log", "-1", "--format=%B", sha)
        files = [l.split("\t") for l in git("show", "--name-status", "--format=", sha).splitlines()]
        tree_items = []
        for st, *path in files:
            path = path[0]
            if st in ("A", "M", "C"):
                content = subprocess.run(["git", "show", f"{sha}:{path}"],
                                         capture_output=True, check=True).stdout
                blob = api("POST", "/git/blobs", token,
                           {"content": base64.b64encode(content).decode(), "encoding": "base64"})
                tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
                print(f"  blob {path} ({len(content)}B)")
            elif st == "D":
                tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
                print(f"  delete {path}")
        tree = api("POST", "/git/trees", token, {"base_tree": parent_tree, "tree": tree_items})
        commit = api("POST", "/git/commits", token,
                     {"message": msg, "tree": tree["sha"], "parents": [parent]})
        parent, parent_tree = commit["sha"], tree["sha"]
        print(f"commit {sha[:7]} -> {commit['sha'][:7]}: {msg.splitlines()[0][:60]}")

    api("PATCH", f"/git/refs/heads/{BRANCH}", token, {"sha": parent, "force": False})
    print(f"OK: {BRANCH} now at {parent[:7]}")


if __name__ == "__main__":
    main()
