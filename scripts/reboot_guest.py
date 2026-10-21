#!/usr/bin/env python3
"""Approved reboot of one patched guest (proposal 3-patching 3.4; migration step 9). Used by ci-reboot.sh.

  reboot_guest.py gate ISSUE STANDARDS APPS_DIR|- OUT.json
      exit 0 = allowed, plan in OUT.json; 2 = refused (reason on stderr; the issue is a valid reboot issue);
      3 = not a 'patch: reboot <host>' issue (no comment is posted); other = error.
      OUT.json.valid is written as soon as the issue is a valid reboot issue.
  reboot_guest.py health PLAN HOST          catalogue health of HOST now (exit 0 = healthy; no entry = SSH reachable)
  reboot_guest.py wait-health PLAN HOST     health, retried until reboot.timeout_s
  reboot_guest.py hook PLAN before|after    drain hook on the hook host (policy drain only; else no-op)
  reboot_guest.py pve-reboot PLAN           Proxmox reboot (PROXMOX_VE_API_TOKEN = apply token) + SSH up + PID 1 restarted
  reboot_guest.py selftest
Env: SERVER, REPO (homelab/homelab-infra), GITEA_API_TOKEN (read infra issues + read apps issues), INFRA_ROOT.
Never touches protected VMIDs (protected.yaml + 100/300/303) or templates."""
import json, os, re, shlex, subprocess, sys, time, urllib.parse

ROOT = os.environ.get("INFRA_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

HOST = re.compile(r"^hm1-[a-z]+-[a-z][a-z0-9]*[0-9]{2}$")
TITLE = re.compile(r"^patch: reboot (hm1-[a-z]+-[a-z][a-z0-9]*[0-9]{2})$")
UNIT = re.compile(r"^[A-Za-z0-9@._:-]{1,120}$")
APPROVER = "faris"
APPROVE_WORDS = {"reboot", "approve"}
PIPELINE_MARKERS = ("<!-- digest:", "<!-- reboot-run:")
HARD_PROTECTED = {100, 300, 303}
APPS_REPO = "homelab/homelab-apps"
POLICIES = {"one_at_a_time", "pair", "drain"}
K3S = "/usr/local/bin/k3s kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml"
DEFAULT_BEFORE = K3S + " drain {node} --ignore-daemonsets --delete-emptydir-data --timeout=300s"
DEFAULT_AFTER = K3S + " uncordon {node}"


class Refused(Exception):
    pass


# ---------------------------------------------------------------- pure logic (selftest covers it)
def norm(body):
    return re.sub(r"[^a-z]", "", (body or "").strip().lower())


def approval(comments):
    """comments: oldest first, [{user, body, created_at}]. Return the approving comment or raise Refused."""
    idx = [i for i, c in enumerate(comments) if c["user"] == APPROVER]
    if not idx:
        raise Refused(f"no answer from {APPROVER} on this issue")
    i = idx[-1]
    if norm(comments[i]["body"]) not in APPROVE_WORDS:
        raise Refused(f"the newest answer from {APPROVER} is not 'reboot'/'approve'")
    for c in comments[i + 1:]:
        if any(m in (c["body"] or "") for m in PIPELINE_MARKERS):
            raise Refused(f"a pipeline comment ({c['created_at']}) is newer than the approval: "
                          f"a new patch run or an earlier reboot attempt; {APPROVER} must answer 'reboot' again")
    return comments[i]


def compose(h):
    parts = [f"systemctl is-active --quiet {shlex.quote(s)}" for s in h["services"]]
    parts.append(f"timeout {int(h['timeout_s'])} bash -c {shlex.quote(h['command'])}")
    return " && ".join(parts)


def health_of(e, where):
    h = e.get("health")
    if not isinstance(h, dict):
        raise ValueError(f"{where}: no health mapping")
    sv = h.get("services") or []
    if not isinstance(sv, list) or any(not isinstance(s, str) or not UNIT.match(s) for s in sv):
        raise ValueError(f"{where}: bad health.services")
    cmd, t = h.get("command"), h.get("timeout_s")
    if not isinstance(cmd, str) or not cmd.strip() or len(cmd) > 8192:
        raise ValueError(f"{where}: bad health.command")
    if not isinstance(t, int) or isinstance(t, bool) or not 1 <= t <= 900:
        raise ValueError(f"{where}: bad health.timeout_s")
    return {"services": sv, "command": cmd, "timeout_s": t}


def policy_plan(host, dep, entry, spec_role):
    """dep: {app, zone, hosts}; entry: catalogue app.yaml; spec_role: host -> role code. Returns the policy part."""
    r = entry.get("reboot") or {}
    pol, tmo = r.get("policy"), r.get("timeout_s", 600)
    if pol not in POLICIES:
        raise ValueError(f"catalog/{dep['app']}: reboot.policy {pol!r} unknown")
    if not isinstance(tmo, int) or isinstance(tmo, bool) or not 60 <= tmo <= 3600:
        raise ValueError(f"catalog/{dep['app']}: reboot.timeout_s must be 60-3600")
    hosts = sorted(dep["hosts"])
    hc = compose(health_of(entry, f"catalog/{dep['app']}"))
    plan = {"app": dep["app"], "zone": dep["zone"], "deployment": f"{dep['zone']}/{dep['app']}", "policy": pol,
            "timeout_s": tmo, "members": hosts, "others": [h for h in hosts if h != host],
            "health": {h: hc for h in hosts}, "before": "", "after": "", "hook_host": ""}
    plan["lock"] = hosts if pol in ("pair", "drain") else [host]
    if pol == "pair" and not plan["others"]:
        raise Refused("policy pair but the deployment has no partner host")
    if pol == "drain":
        roles = (entry.get("catalog") or {}).get("role")
        roles = roles if isinstance(roles, list) else [roles]
        ctl = [h for h in hosts if spec_role.get(h) == roles[0]]
        if not ctl:
            raise Refused(f"policy drain: no {roles[0]} host in the deployment to run the hook on")
        plan["hook_host"] = ctl[0]
        for k, d in (("before", DEFAULT_BEFORE), ("after", DEFAULT_AFTER)):
            v = r.get(k, d)
            if not isinstance(v, str) or not v.strip() or len(v) > 2048:
                raise ValueError(f"catalog/{dep['app']}: bad reboot.{k}")
            plan[k] = v.replace("{node}", host)
    return plan


# ---------------------------------------------------------------- I/O
def gitea_get(path):
    import gitea
    return gitea.api("GET", path)


def comments_of(repo, n):
    out, page = [], 1
    while page <= 20:
        cs = gitea_get(f"/repos/{repo}/issues/{n}/comments?limit=50&page={page}") or []
        out += [{"user": (c.get("user") or {}).get("login", ""), "body": c.get("body") or "",
                 "created_at": c.get("created_at", "")} for c in cs]
        if len(cs) < 50:
            break
        page += 1
    out.sort(key=lambda c: c["created_at"])
    return out


def open_app_verify(app, hosts):
    found = []
    for h in hosts:
        t = f"app-verify: {app}/{h}"
        for i in gitea_get(f"/repos/{APPS_REPO}/issues?state=open&type=issues&limit=50&q={urllib.parse.quote(t)}") or []:
            if i.get("title") == t:
                found.append(f"#{i['number']} {t}")
    return found


def deployments_of(apps_dir, host):
    import yaml
    deps = []
    for p in sorted(__import__("pathlib").Path(apps_dir).glob("deployments/*/*.yaml")):
        if p.is_symlink():
            raise ValueError(f"{p}: symlink refused")
        d = yaml.safe_load(p.read_text())
        if not isinstance(d, dict) or not isinstance(d.get("hosts") or [], list):
            raise ValueError(f"{p}: malformed")
        hs = d.get("hosts") or []
        if any(not isinstance(h, str) or not HOST.match(h) for h in hs):
            raise ValueError(f"{p}: bad hostname")
        if host in hs:
            deps.append({"app": str(d.get("app")), "zone": p.parent.name, "hosts": hs})
    return deps


def gate(issue, std, apps_dir, out):
    import yaml
    from specs import load_specs
    if not re.fullmatch(r"[0-9]{1,6}", issue):
        print("ISSUE must be a number", file=sys.stderr); sys.exit(3)
    repo = os.environ["REPO"]
    i = gitea_get(f"/repos/{repo}/issues/{issue}")
    m = TITLE.match((i or {}).get("title", ""))
    if not i or i.get("pull_request") or not m:
        print(f"#{issue} is not a 'patch: reboot <host>' issue", file=sys.stderr); sys.exit(3)
    host = m.group(1)
    open(out + ".valid", "w").write(host + "\n")
    warnings = []
    try:
        if i.get("state") != "open":
            raise Refused("the issue is closed")
        ok = approval(comments_of(repo, issue))
        specs = {s["hostname"]: s for s in load_specs()}
        s = specs.get(host)
        if not s:
            raise Refused(f"{host} has no guest spec")
        vmid = int(s["vmid"])
        prot = yaml.safe_load(open(os.path.join(std, "protected.yaml"))) or {}
        never = {int(v) for v in prot.get("vmids", {})} | HARD_PROTECTED
        lo, hi = prot.get("templates", [9000, 9199])
        if vmid in never or lo <= vmid <= hi:
            raise Refused(f"vmid {vmid} is protected or a template: never rebooted by the pipeline")
        plan = {"host": host, "vmid": vmid, "kind": "qemu" if s.get("type", "lxc") == "vm" else "lxc",
                "ip": {h: str(x["ip"]) for h, x in specs.items()}, "approved_at": ok["created_at"],
                "app": "", "deployment": "", "policy": "one_at_a_time", "timeout_s": 600, "members": [host],
                "others": [], "health": {}, "before": "", "after": "", "hook_host": "", "lock": [host]}
        deps = []
        if apps_dir == "-":
            warnings.append("homelab-apps clone unavailable: one_at_a_time, L1-L3 only (fallback)")
        else:
            try:
                deps = deployments_of(apps_dir, host)
            except Exception as e:
                warnings.append(f"homelab-apps deployments unreadable ({e}): one_at_a_time, L1-L3 only (fallback)")
        if len(deps) > 1:
            raise Refused(f"{host} is in more than one deployment: " + ", ".join(f"{d['zone']}/{d['app']}" for d in deps))
        if deps:
            d = deps[0]
            for h in d["hosts"]:
                if h not in specs:
                    warnings.append(f"deployment host {h} has no spec: ignored")
            d["hosts"] = [h for h in d["hosts"] if h in specs]
            if any(int(specs[h]["vmid"]) in never for h in d["hosts"]):
                raise Refused("the deployment lists a protected guest")
            entry = yaml.safe_load(open(os.path.join(apps_dir, "catalog", d["app"], "app.yaml")))
            plan.update(policy_plan(host, d, entry, {h: specs[h].get("role") for h in d["hosts"]}))
            av = open_app_verify(d["app"], d["hosts"])
            if av:
                raise Refused("open app-verify issue(s) in this deployment: " + "; ".join(av))
        plan["warnings"] = warnings
    except Refused as e:
        print(str(e), file=sys.stderr)
        for w in warnings:
            print(f"warning: {w}", file=sys.stderr)
        sys.exit(2)
    json.dump(plan, open(out, "w"), indent=1)
    print(f"{host}: policy {plan['policy']}, lock {','.join(plan['lock'])}")


def ssh(ip, script, timeout):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new",
                           f"root@{ip}", "bash -s"], input=script, capture_output=True, text=True, timeout=timeout)


def health(plan, host, quiet=False):
    p = json.load(open(plan)) if isinstance(plan, str) else plan
    script = p["health"].get(host) or "true"
    try:
        r = ssh(p["ip"][host], script + "\n", 960)
        ok = r.returncode == 0
        why = " ".join((r.stdout + r.stderr).split())[-200:]
    except subprocess.TimeoutExpired:
        ok, why = False, "ssh timeout"
    if not quiet:
        print(f"{host} health {'ok' if ok else 'FAIL'} {why}")
    return ok


def wait_health(plan, host):
    p = json.load(open(plan))
    end = time.time() + p["timeout_s"]
    while True:
        if health(p, host, quiet=True):
            print(f"{host} health ok"); return True
        if time.time() > end:
            health(p, host); print(f"{host} not healthy within {p['timeout_s']} s"); return False
        time.sleep(10)


def hook(plan, which):
    p = json.load(open(plan))
    cmd = p.get(which) or ""
    if p["policy"] != "drain" or not cmd:
        print(f"no {which} hook"); return True
    try:
        r = ssh(p["ip"][p["hook_host"]], f"timeout 330 bash -c {shlex.quote(cmd)}\n", 360)
    except subprocess.TimeoutExpired:
        print(f"{which} hook on {p['hook_host']}: ssh timeout"); return False
    print(f"{which} hook on {p['hook_host']}: rc {r.returncode} " + " ".join((r.stdout + r.stderr).split())[-300:])
    return r.returncode == 0


def pid1_start(ip):
    try:
        r = ssh(ip, "ps -o lstart= -p 1\n", 30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except subprocess.TimeoutExpired:
        return ""


def pve_reboot(plan):
    from snapshot import NODE, NEVER, TEMPLATES, ssh_up, task
    p = json.load(open(plan))
    vmid, kind, ip = int(p["vmid"]), p["kind"], p["ip"][p["host"]]
    if vmid in NEVER or vmid in TEMPLATES:
        sys.exit(f"vmid {vmid} protected/template: refused")
    before = pid1_start(ip)
    ok, why = task("POST", f"/nodes/{NODE}/{kind}/{vmid}/status/reboot", {"timeout": 300})
    if not ok:
        sys.exit(f"proxmox reboot of {vmid} failed: {why}")
    if not ssh_up(ip, 300):
        sys.exit(f"{p['host']} rebooted but SSH not up after 300 s")
    after = ""
    for _ in range(12):
        after = pid1_start(ip)
        if after:
            break
        time.sleep(5)
    if before and after and before == after:
        sys.exit(f"{p['host']}: PID 1 start time unchanged ({after}): the guest did not reboot")
    print(f"{p['host']} rebooted (vmid {vmid}); PID 1 started {before or '?'} -> {after or '?'}")


def selftest():
    bad = []
    def c(u, b, t):
        return {"user": u, "body": b, "created_at": t}
    cases = [
        ([c("ci", "pending <!-- digest:ab -->", "1"), c("faris", "Reboot", "2")], True, "plain approval"),
        ([c("faris", "approve.", "2"), c("oc-pm", "noted", "3")], True, "agent comment after approval is ignored"),
        ([c("faris", "reboot", "2"), c("faris", "defer", "3")], False, "later defer wins"),
        ([c("faris", "reboot", "2"), c("ci", "new run <!-- digest:cd -->", "3")], False, "new patch run after approval"),
        ([c("faris", "reboot", "2"), c("ci", "x <!-- reboot-run:1 -->", "3")], False, "approval already used"),
        ([c("oc-pm", "reboot", "2")], False, "only faris approves"),
        ([c("faris", "reboot it later", "2")], False, "not exactly reboot"),
        ([c("faris", "no reboot", "2")], False, "no reboot"),
    ]
    for cs, want, why in cases:
        try:
            approval(cs); got = True
        except Refused:
            got = False
        if got != want:
            bad.append(f"approval: {why}")
    hl = {"services": [], "command": "true", "timeout_s": 5}
    k3s = {"catalog": {"role": ["k3sc", "k3sw"]}, "health": hl, "reboot": {"policy": "drain", "timeout_s": 600}}
    dep = {"app": "k3s", "zone": "shr", "hosts": ["hm1-shr-k3sw01", "hm1-shr-k3sc02", "hm1-shr-k3sc01"]}
    roles = {"hm1-shr-k3sc01": "k3sc", "hm1-shr-k3sc02": "k3sc", "hm1-shr-k3sw01": "k3sw"}
    p = policy_plan("hm1-shr-k3sw01", dep, k3s, roles)
    if p["hook_host"] != "hm1-shr-k3sc01" or "drain hm1-shr-k3sw01 " not in p["before"] or len(p["lock"]) != 3:
        bad.append(f"drain plan: {p}")
    dns = {"catalog": {"role": "dns"}, "health": hl, "reboot": {"policy": "pair", "timeout_s": 600}}
    p = policy_plan("hm1-wifi-dns01", {"app": "adguard", "zone": "wifi", "hosts": ["hm1-wifi-dns01", "hm1-wifi-dns02"]}, dns, {})
    if p["others"] != ["hm1-wifi-dns02"] or p["before"] or p["lock"] != ["hm1-wifi-dns01", "hm1-wifi-dns02"]:
        bad.append(f"pair plan: {p}")
    for e, why in [({**dns, "reboot": {"policy": "yolo"}}, "unknown policy"),
                   ({**dns, "reboot": {"policy": "pair", "timeout_s": 5}}, "timeout too small")]:
        try:
            policy_plan("hm1-wifi-dns01", {"app": "adguard", "zone": "wifi", "hosts": ["hm1-wifi-dns01", "hm1-wifi-dns02"]}, e, {})
            bad.append(f"accepted: {why}")
        except (ValueError, Refused):
            pass
    try:
        policy_plan("hm1-stg-dns01", {"app": "adguard", "zone": "stg", "hosts": ["hm1-stg-dns01"]}, dns, {})
        bad.append("accepted: pair without partner")
    except Refused:
        pass
    print("\n".join(bad) or "selftest ok")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    cmd, a = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if cmd == "gate" and len(a) == 4:
        gate(*a)
    elif cmd == "health" and len(a) == 2:
        sys.exit(0 if health(*a) else 1)
    elif cmd == "wait-health" and len(a) == 2:
        sys.exit(0 if wait_health(*a) else 1)
    elif cmd == "hook" and len(a) == 2 and a[1] in ("before", "after"):
        sys.exit(0 if hook(*a) else 1)
    elif cmd == "pve-reboot" and len(a) == 1:
        pve_reboot(*a)
    elif cmd == "selftest":
        selftest()
    else:
        sys.exit(__doc__)
