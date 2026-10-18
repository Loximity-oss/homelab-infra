#!/usr/bin/env python3
"""Pre-change snapshots of existing guests (design-v7 decision 32, D3). Apply token (VM.Snapshot[.Rollback]).
  snapshot.py take     --name pre-apply_YYYYmmdd_HHMM --hosts h1,h2    (ci-apply.sh)
  snapshot.py take     --name pre-patch_YYYYmmdd_HHMM --hosts h1,h2    (ci-patch.sh, ring-1 patching)
  snapshot.py rollback --name <snap> --hosts h1,h2        (guest is started again afterwards)
  snapshot.py prune    --days 7 [--prefix pre-apply_]     (pre-apply_* and pre-patch_* older than N days, all pipeline guests)
Protected VMIDs (100, 300, 303) and the template range are never touched, whatever the specs say.
Prints one line per host: <host> ok|fail <detail>. Exit 1 if any host failed."""
import argparse, datetime, json, os, re, socket, ssl, sys, time, urllib.error, urllib.parse, urllib.request

ROOT = os.environ.get("INFRA_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from specs import load_specs  # noqa: E402

PVE, NODE = "https://192.168.67.2:8006/api2/json", "hm1-mgt-pve01"
PREFIXES = ("pre-apply_", "pre-patch_")          # pre-apply_ = ci-apply.sh, pre-patch_ = ci-patch.sh
NAME_RE = re.compile(r"^pre-(apply|patch)_[0-9]{8}_[0-9]{4}$")
NEVER = {100, 300, 303}                           # protected.yaml: no snapshots by the pipeline
TEMPLATES = range(9000, 9200)                     # protected.yaml templates: clone-only
_ctx = ssl._create_unverified_context()  # PVE API self-signed until the internal PKI covers it (provider: insecure)


def api(method, path, data=None):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(PVE + path, data=body, method=method,
                                 headers={"Authorization": "PVEAPIToken=" + os.environ["PROXMOX_VE_API_TOKEN"]})
    return json.load(urllib.request.urlopen(req, context=_ctx, timeout=60))["data"]


def task(method, path, data=None, tries=6):
    """Start a task and wait for it; retry while the guest is locked (e.g. right after a rollback + start)."""
    why = ""
    for _ in range(tries):
        try:
            ok, why = wait(api(method, path, data))
        except urllib.error.HTTPError as e:
            ok, why = False, e.read().decode(errors="replace")[:200]
        if ok or "lock" not in str(why).lower():
            return ok, why
        time.sleep(10)
    return False, why


def ssh_up(ip, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((ip, 22), timeout=5):
                return True
        except OSError:
            time.sleep(5)
    return False


def wait(upid, timeout=600):
    end = time.time() + timeout
    while time.time() < end:
        st = api("GET", f"/nodes/{NODE}/tasks/{urllib.parse.quote(upid, safe='')}/status")
        if st.get("status") == "stopped":
            return st.get("exitstatus") == "OK", st.get("exitstatus")
        time.sleep(2)
    return False, "timeout"


def guests(hosts=None):
    out = {}
    for s in load_specs():
        vmid = int(s["vmid"])
        if vmid in NEVER or vmid in TEMPLATES:
            print(f"{s['hostname']} skip protected/template vmid {vmid}", file=sys.stderr)
            continue
        if hosts is None or s["hostname"] in hosts:
            out[s["hostname"]] = (vmid, "qemu" if s.get("type", "lxc") == "vm" else "lxc", str(s["ip"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["take", "rollback", "prune"])
    ap.add_argument("--name", default="")
    ap.add_argument("--hosts", default="")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--prefix", default="", choices=["", *PREFIXES])   # prune: empty = every pipeline prefix
    a = ap.parse_args()
    bad = 0
    if a.action == "prune":
        cutoff = time.time() - a.days * 86400
        prefixes = (a.prefix,) if a.prefix else PREFIXES
        for h, (vmid, kind, _ip) in guests().items():
            try:
                for sn in api("GET", f"/nodes/{NODE}/{kind}/{vmid}/snapshot"):
                    if sn.get("name", "").startswith(prefixes) and sn.get("snaptime", time.time()) < cutoff:
                        ok, why = task("DELETE", f"/nodes/{NODE}/{kind}/{vmid}/snapshot/{sn['name']}")
                        print(f"{h} {'ok' if ok else 'fail'} deleted {sn['name']} {'' if ok else why}")
                        bad += not ok
            except Exception as e:  # guest not created yet / gone
                print(f"{h} skip {e}")
        sys.exit(1 if bad else 0)
    if a.action == "take" and not NAME_RE.match(a.name):
        sys.exit("snapshot name must be pre-apply_YYYYmmdd_HHMM or pre-patch_YYYYmmdd_HHMM")
    if not a.name.startswith(PREFIXES):
        sys.exit(f"snapshot name must start with one of {PREFIXES}")
    what = "patch" if a.name.startswith("pre-patch_") else "apply"
    want = [h for h in a.hosts.split(",") if h]
    for h, (vmid, kind, ip) in guests(set(want)).items():
        try:
            if a.action == "take":
                data = {"snapname": a.name, "description": f"homelab-infra {what} {datetime.datetime.now():%Y-%m-%d %H:%M}"}
                if kind == "qemu":
                    data["vmstate"] = 0
                ok, why = task("POST", f"/nodes/{NODE}/{kind}/{vmid}/snapshot", data)
            else:
                ok, why = task("POST", f"/nodes/{NODE}/{kind}/{vmid}/snapshot/{a.name}/rollback", {"start": 1})
                if ok and not ssh_up(ip):
                    ok, why = False, "rolled back but SSH not up after 180 s"
            print(f"{h} {'ok' if ok else 'fail'} {a.action} {a.name} {'' if ok else why}")
            bad += not ok
        except Exception as e:
            print(f"{h} fail {a.action}: {e}")
            bad += 1
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
