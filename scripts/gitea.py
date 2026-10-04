#!/usr/bin/env python3
"""Small Gitea API client for CI (token: GITEA_API_TOKEN, server: SERVER, repo: REPO).
Subcommands:
  comment N FILE            add a comment (markdown file)
  labels N L1,L2            replace class/wip labels on the PR (others kept)
  review-request N USER     request a review
  automerge N SHA           merge when checks succeed (ci-merger)
  update-branch N           merge main into the PR branch
  pr-of-commit SHA          print "number head_sha" of the PR that produced a commit
  issue-upsert TITLE LABEL FILE   open or comment on an open issue with this title
"""
import json, os, sys, urllib.request, urllib.error

S, R, T = os.environ["SERVER"].rstrip("/"), os.environ["REPO"], os.environ["GITEA_API_TOKEN"]


def api(method, path, body=None, ok404=False):
    req = urllib.request.Request(f"{S}/api/v1{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"token {T}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
            return json.loads(data) if data else None
    except urllib.error.HTTPError as e:
        if ok404 and e.code == 404:
            return None
        sys.exit(f"gitea {method} {path}: {e.code} {e.read().decode()[:300]}")


def label_ids(names):
    have = {l["name"]: l["id"] for l in api("GET", f"/repos/{R}/labels?limit=100")}
    return [have[n] for n in names if n in have]


def main(cmd, *a):
    if cmd == "comment":
        api("POST", f"/repos/{R}/issues/{a[0]}/comments", {"body": open(a[1]).read()})
    elif cmd == "labels":
        n, want = a[0], [x for x in a[1].split(",") if x]
        cur = [l["name"] for l in api("GET", f"/repos/{R}/issues/{n}/labels")]
        keep = [l for l in cur if not (l.startswith("class/") or l == "wip")]
        api("PUT", f"/repos/{R}/issues/{n}/labels", {"labels": label_ids(sorted(set(keep + want)))})
    elif cmd == "review-request":
        api("POST", f"/repos/{R}/pulls/{a[0]}/requested_reviewers", {"reviewers": [a[1]]})
    elif cmd == "automerge":
        api("POST", f"/repos/{R}/pulls/{a[0]}/merge",
            {"Do": "merge", "head_commit_id": a[1], "merge_when_checks_succeed": True, "delete_branch_after_merge": True})
    elif cmd == "update-branch":
        api("POST", f"/repos/{R}/pulls/{a[0]}/update?style=merge")
    elif cmd == "pr-of-commit":
        pr = api("GET", f"/repos/{R}/commits/{a[0]}/pull", ok404=True)
        print(f"{pr['number']} {pr['head']['sha']}" if pr else "")
    elif cmd == "issue-upsert":
        title, label, body = a[0], a[1], open(a[2]).read()
        for i in api("GET", f"/repos/{R}/issues?state=open&type=issues&limit=50"):
            if i["title"] == title:
                api("POST", f"/repos/{R}/issues/{i['number']}/comments", {"body": body}); return
        api("POST", f"/repos/{R}/issues", {"title": title, "body": body, "labels": label_ids([label])})
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(*sys.argv[1:])
