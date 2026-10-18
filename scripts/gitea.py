#!/usr/bin/env python3
"""Small Gitea API client for CI (token: GITEA_API_TOKEN, server: SERVER, repo: REPO).
Subcommands:
  comment N FILE            add a comment (markdown file)
  labels N L1,L2            replace class/wip labels on the PR (others kept)
  review-request N USER     request a review
  automerge N SHA           merge when checks succeed (ci-merger)
  update-branch N           merge main into the PR branch
  refresh-open [EXCEPT]     update every open, classified, non-WIP PR behind main (re-plan after main moved)
  pr-of-commit SHA          print "number head_sha" of the PR that produced a commit
  issue-upsert TITLE LABEL FILE   open or comment on an open issue with this title
  issue-ensure TITLE LABEL FILE   open once; comment only if FILE's <!-- digest:x --> marker is new (no 6-hourly spam)
  issue-close TITLE TEXT    comment and close the open issue with this title (if any)
  pr-find BRANCH            print "number state merged" of the newest PR from BRANCH (any state), or nothing
  pr-open BRANCH TITLE FILE open a PR BRANCH -> main, print its number
  pr-gate BRANCH USER       exit 0 and print the number if a PR from BRANCH is merged with a current APPROVED
                            review by USER (not dismissed, not stale); otherwise exit 1
"""
import json, os, re, sys, urllib.parse, urllib.request, urllib.error

S, R, T = os.environ["SERVER"].rstrip("/"), os.environ["REPO"], os.environ["GITEA_API_TOKEN"]


def api(method, path, body=None, ok404=False, ok_codes=()):
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
        if e.code in ok_codes:
            return {"_status": e.code, "_body": e.read().decode()[:300]}
        sys.exit(f"gitea {method} {path}: {e.code} {e.read().decode()[:300]}")


def label_ids(names):
    have = {l["name"]: l["id"] for l in api("GET", f"/repos/{R}/labels?limit=100")}
    return [have[n] for n in names if n in have]


def open_issue(title):
    q = urllib.parse.quote(title)
    for i in api("GET", f"/repos/{R}/issues?state=open&type=issues&limit=50&q={q}") or []:
        if i["title"] == title:
            return i
    return None


def pulls_from(branch, state):
    for page in range(1, 6):
        ps = api("GET", f"/repos/{R}/pulls?state={state}&sort=recentupdate&limit=50&page={page}") or []
        for p in ps:
            if (p.get("head") or {}).get("ref") == branch:
                yield p
        if len(ps) < 50:
            return


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
        # 409 = already scheduled by an earlier run of this PR (re-plan after an update): keep that schedule.
        r = api("POST", f"/repos/{R}/pulls/{a[0]}/merge",
                {"Do": "merge", "head_commit_id": a[1], "merge_when_checks_succeed": True, "delete_branch_after_merge": True},
                ok_codes=(409,))
        if r and r.get("_status") == 409:
            print(f"automerge: already scheduled ({r['_body'][:120]})")
    elif cmd == "update-branch":
        api("POST", f"/repos/{R}/pulls/{a[0]}/update?style=merge")
    elif cmd == "refresh-open":
        # A saved plan is only valid for the state it was made against. After an apply, every open planned PR is
        # updated from main, which triggers a fresh plan (issue #26: stale plan after a parallel merge).
        skip = a[0] if a else ""
        for p in api("GET", f"/repos/{R}/pulls?state=open&limit=50"):
            labels = [l["name"] for l in p.get("labels", [])]
            if str(p["number"]) == skip or "wip" in labels or not any(l.startswith("class/") for l in labels):
                continue
            r = api("POST", f"/repos/{R}/pulls/{p['number']}/update?style=merge", ok_codes=(409, 422))
            print(f"PR #{p['number']}: " + ("updated from main" if not r else f"not updated ({r['_status']}: {r['_body'][:100]})"))
    elif cmd == "pr-of-commit":
        pr = api("GET", f"/repos/{R}/commits/{a[0]}/pull", ok404=True)
        print(f"{pr['number']} {pr['head']['sha']}" if pr else "")
    elif cmd == "issue-close":
        for i in api("GET", f"/repos/{R}/issues?state=open&type=issues&limit=50"):
            if i["title"] == a[0]:
                api("POST", f"/repos/{R}/issues/{i['number']}/comments", {"body": a[1]})
                api("PATCH", f"/repos/{R}/issues/{i['number']}", {"state": "closed"})
    elif cmd == "issue-upsert":
        title, label, body = a[0], a[1], open(a[2]).read()
        for i in api("GET", f"/repos/{R}/issues?state=open&type=issues&limit=50"):
            if i["title"] == title:
                api("POST", f"/repos/{R}/issues/{i['number']}/comments", {"body": body}); return
        api("POST", f"/repos/{R}/issues", {"title": title, "body": body, "labels": label_ids([label])})
    elif cmd == "issue-ensure":
        title, label, body = a[0], a[1], open(a[2]).read()
        m = re.search(r"<!-- digest:[0-9a-f]+ -->", body)
        i = open_issue(title)
        if not i:
            api("POST", f"/repos/{R}/issues", {"title": title, "body": body, "labels": label_ids([label])}); return
        seen = [i.get("body") or ""] + [c.get("body") or "" for c in api("GET", f"/repos/{R}/issues/{i['number']}/comments") or []]
        if not m or not any(m.group(0) in s for s in seen):
            api("POST", f"/repos/{R}/issues/{i['number']}/comments", {"body": body})
    elif cmd == "pr-find":
        p = next(pulls_from(a[0], "all"), None)
        print(f"{p['number']} {p['state']} {str(bool(p.get('merged'))).lower()}" if p else "")
    elif cmd == "pr-open":
        p = api("POST", f"/repos/{R}/pulls", {"head": a[0], "base": "main", "title": a[1], "body": open(a[2]).read()})
        print(p["number"])
    elif cmd == "pr-gate":
        branch, user = a[0], a[1]
        for p in pulls_from(branch, "closed"):
            if not p.get("merged"):
                continue
            revs = api("GET", f"/repos/{R}/pulls/{p['number']}/reviews") or []
            if any((r.get("user") or {}).get("login") == user and r.get("state") == "APPROVED"
                   and not r.get("dismissed") and not r.get("stale") for r in revs):
                print(p["number"]); return
            sys.exit(f"{branch}: PR #{p['number']} merged without a current approval by {user}")
        sys.exit(f"{branch}: no merged PR")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(*sys.argv[1:])
