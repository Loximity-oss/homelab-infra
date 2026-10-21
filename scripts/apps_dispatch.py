#!/usr/bin/env python3
"""Cross-repo dispatch: infra apply -> homelab-apps `deploy` (proposal 2-orchestration 2.1, migration step 9).
Used by ci-apply.sh only when APPS_DISPATCH=1 (off until cutover step 10(d)).

  apps_dispatch.py hosts APPS_DIR VERIFY_JSON VHOSTS ROLLED_BACK
      csv on stdout (may be empty): VHOSTS with verify rows and no failed row, minus ROLLED_BACK (stg auto-rollback),
      that appear in a homelab-apps deployment and have an infra spec. A missing verify.json = nothing verified = empty.
      Malformed deployments -> exit 1 (ci-apply.sh warns; never an apply failure).
  apps_dispatch.py send HOSTS INFRA_PR
      POST /repos/homelab/homelab-apps/actions/workflows/deploy.yml/dispatches (ref main, inputs hosts + infra_pr).
      Env: SERVER, APPS_DISPATCH_TOKEN (ci-apps bot, hm1/platform/ci/apply gitea_apps_dispatch_token). Exit 1 on error.
  apps_dispatch.py selftest
      offline unit test of the host selection.

The apps side treats the inputs as untrusted (deploy refuses any ref but main and intersects hosts with deployments
and specs), so this script is not a security boundary; it only keeps the dispatch small and correct."""
import json, os, pathlib, re, sys, urllib.error, urllib.request

HOST = re.compile(r"^hm1-[a-z]+-[a-z][a-z0-9]*[0-9]{2}$")
APPS_REPO = "homelab/homelab-apps"
WORKFLOW = "deploy.yml"


def deployment_hosts(root):
    """Hosts of every deployments/<zone>/<app>.yaml (same strictness as apps_hosts.py)."""
    import yaml
    root = pathlib.Path(root)
    if not (root / ".git").exists() and not (root / "README.md").exists():
        sys.exit(f"{root}: not a homelab-apps checkout")
    hosts = set()
    for p in sorted(root.glob("deployments/*/*.yaml")):
        if p.is_symlink():
            sys.exit(f"{p}: symlink refused")
        d = yaml.safe_load(p.read_text())
        if not isinstance(d, dict):
            sys.exit(f"{p}: not a mapping")
        hs = d.get("hosts") or []
        if not isinstance(hs, list):
            sys.exit(f"{p}: hosts is not a list")
        for h in hs:
            if not isinstance(h, str) or not HOST.match(h):
                sys.exit(f"{p}: bad hostname {h!r}")
            hosts.add(h)
    return hosts


def select(deployed, rows, vhosts, rolled_back, spec_hosts):
    """Pure selection rule (infra R3-4): tested, nothing failed, not rolled back, in a deployment, has a spec."""
    tested = {r.get("host") for r in rows}
    failed = {r.get("host") for r in rows if r.get("status") == "fail"}
    green = (set(vhosts) & tested) - failed - set(rolled_back)
    return sorted(h for h in green & set(deployed) & set(spec_hosts) if HOST.match(h))


def csv(s):
    return [x for x in (s or "").split(",") if x and x != "-"]


def hosts(apps_dir, verify_json, vhosts, rolled_back):
    root = os.environ.get("INFRA_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "scripts"))
    from specs import load_specs
    try:
        rows = json.load(open(verify_json)).get("rows") or []
    except (OSError, ValueError):
        print(f"{verify_json}: missing or unreadable: nothing verified, nothing dispatched", file=sys.stderr)
        rows = []
    spec_hosts = {s["hostname"] for s in load_specs()}
    print(",".join(select(deployment_hosts(apps_dir), rows, csv(vhosts), csv(rolled_back), spec_hosts)))


def send(hs, infra_pr):
    want = csv(hs)
    if not want or any(not HOST.match(h) for h in want):
        sys.exit(f"bad hosts {hs!r}")
    if not re.fullmatch(r"[0-9]{1,6}", infra_pr or ""):
        sys.exit(f"bad infra_pr {infra_pr!r}")
    server, token = os.environ["SERVER"].rstrip("/"), os.environ.get("APPS_DISPATCH_TOKEN", "")
    if not token:
        sys.exit("APPS_DISPATCH_TOKEN is empty")
    body = {"ref": "main", "inputs": {"hosts": ",".join(want), "infra_pr": infra_pr}}
    req = urllib.request.Request(f"{server}/api/v1/repos/{APPS_REPO}/actions/workflows/{WORKFLOW}/dispatches",
                                 method="POST", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"token {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status not in (200, 201, 204):
                sys.exit(f"dispatch: unexpected HTTP {r.status}")
    except urllib.error.HTTPError as e:
        sys.exit(f"dispatch: HTTP {e.code} {e.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"dispatch: {e.reason}")
    print(f"dispatched {APPS_REPO} {WORKFLOW} on main for {','.join(want)} (infra PR #{infra_pr})")


def selftest():
    a, b, c, s = "hm1-wifi-dns01", "hm1-wifi-dns02", "hm1-stg-dns01", "hm1-int-web01"
    rows = [{"host": a, "status": "pass"}, {"host": b, "status": "pass"}, {"host": b, "status": "fail"},
            {"host": c, "status": "pass"}, {"host": s, "status": "pass"}, {"host": "platform", "status": "pass"}]
    dep, specs = {a, b, c}, {a, b, c, s}
    cases = [
        (select(dep, rows, [a, b, c, s], [], specs), sorted([a, c]), "failed row and non-deployment host dropped"),
        (select(dep, rows, [a, b, c, s], [c], specs), [a], "rolled-back host dropped"),
        (select(dep, rows, [b], [], specs), [], "only a failed host"),
        (select(dep, [], [a], [], specs), [], "no verify rows = nothing"),
        (select(dep, rows, [a], [], {c}), [], "no spec = nothing"),
        (select(dep, rows, ["platform"], [], specs | {"platform"}), [], "pseudo host never dispatched"),
    ]
    bad = [f"{why}: got {got} want {want}" for got, want, why in cases if got != want]
    print("\n".join(bad) or f"selftest ok ({len(cases)} cases)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if cmd == "hosts" and len(args) == 4:
        hosts(*args)
    elif cmd == "send" and len(args) == 2:
        send(*args)
    elif cmd == "selftest":
        selftest()
    else:
        sys.exit(__doc__)
