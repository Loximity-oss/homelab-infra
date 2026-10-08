#!/usr/bin/env python3
"""Pre-change snapshots of existing guests (design-v7 decision 32, D3). Apply token (VM.Snapshot[.Rollback]).
  snapshot.py take     --name pre-apply_YYYYmmdd_HHMM --hosts h1,h2
  snapshot.py rollback --name <snap> --hosts h1,h2        (guest is started again afterwards)
  snapshot.py prune    --days 7                           (pre-apply_* older than N days, all pipeline guests)
Prints one line per host: <host> ok|fail <detail>. Exit 1 if any host failed."""
import argparse, datetime, json, os, socket, ssl, sys, time, urllib.error, urllib.parse, urllib.request

ROOT = os.environ.get("INFRA_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from specs import load_specs  # noqa: E402

PVE, NODE, PREFIX = "https://192.168.67.2:8006/api2/json", "hm1-mgt-pve01", "pre-apply_"
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
        if hosts is None or s["hostname"] in hosts:
            out[s["hostname"]] = (int(s["vmid"]), "qemu" if s.get("type", "lxc") == "vm" else "lxc", str(s["ip"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["take", "rollback", "prune"])
    ap.add_argument("--name", default="")
    ap.add_argument("--hosts", default="")
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    bad = 0
    if a.action == "prune":
        cutoff = time.time() - a.days * 86400
        for h, (vmid, kind, _ip) in guests().items():
            try:
                for sn in api("GET", f"/nodes/{NODE}/{kind}/{vmid}/snapshot"):
                    if sn.get("name", "").startswith(PREFIX) and sn.get("snaptime", time.time()) < cutoff:
                        ok, why = task("DELETE", f"/nodes/{NODE}/{kind}/{vmid}/snapshot/{sn['name']}")
                        print(f"{h} {'ok' if ok else 'fail'} deleted {sn['name']} {'' if ok else why}")
                        bad += not ok
            except Exception as e:  # guest not created yet / gone
                print(f"{h} skip {e}")
        sys.exit(1 if bad else 0)
    if not a.name.startswith(PREFIX):
        sys.exit(f"snapshot name must start with {PREFIX}")
    want = [h for h in a.hosts.split(",") if h]
    for h, (vmid, kind, ip) in guests(set(want)).items():
        try:
            if a.action == "take":
                data = {"snapname": a.name, "description": f"homelab-infra apply {datetime.datetime.now():%Y-%m-%d %H:%M}"}
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
