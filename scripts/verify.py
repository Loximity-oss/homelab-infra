#!/usr/bin/env python3
"""Guest deployment tests (design-v7 decision 32): L1 platform, L2 reachability, L3 OS baseline (Goss via Ansible),
and the "gone" check for destroyed guests. Read-only except L3 copying Goss + the rendered baseline to the guest.

Usage: verify.py --standards DIR [--hosts h1,h2 | --all] [--gone host:vmid:ip,...] --out DIR [--no-l3]
Env:   PROXMOX_VE_API_TOKEN (plan token: read-only), INFRA_ROOT (repo checkout). L2/L3 need the apply runner's SSH key.
Writes DIR/verify.json and DIR/verify.md; exit 1 if any check failed (skips are reported as ⚠️, never as ✅)."""
import argparse, json, os, re, socket, ssl, subprocess, sys, urllib.request
import yaml

ROOT = os.environ.get("INFRA_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from specs import load_specs  # noqa: E402

PVE = "https://192.168.67.2:8006/api2/json"
NODE = "hm1-mgt-pve01"
VM_DEFAULTS = {"cores": 2, "memory_mb": 2048, "disk_gb": 16}   # decision 32 D6 (vm-guests module)
_ctx = ssl._create_unverified_context()  # PVE API self-signed until the internal PKI covers it (provider: insecure)


def api(path):
    req = urllib.request.Request(PVE + path, headers={"Authorization": "PVEAPIToken=" + os.environ["PROXMOX_VE_API_TOKEN"]})
    return json.load(urllib.request.urlopen(req, context=_ctx, timeout=20))["data"]


class Report:
    def __init__(self):
        self.rows = []

    def add(self, host, layer, check, status, evidence=""):   # status: pass | fail | skip
        self.rows.append({"host": host, "layer": layer, "check": check, "status": status,
                          "evidence": " ".join(str(evidence).split())[:300]})

    def ok(self, host, layer, check, cond, evidence=""):
        self.add(host, layer, check, "pass" if cond else "fail", evidence)

    @property
    def failed(self):
        return [r for r in self.rows if r["status"] == "fail"]


def kv(s):  # "virtio=..,bridge=vmbr1,tag=60" -> dict
    out = {}
    for part in (s or "").split(","):
        k, _, v = part.partition("=")
        out[k] = v if _ else ""
    return out


def size_gb(s):
    m = re.search(r"size=(\d+)([GMT])", s or "")
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2)
    return n * 1024 if u == "T" else n // 1024 if u == "M" else n


def expected_tags(s, zone):
    t = {f"zone-{zone}", f"role-{s['role']}", "mgd-tf", f"owner-{s['owner']}", f"os-{s['os']}", f"patch-{s['patch']}",
         f"env-{s.get('env', 'staging' if zone == 'stg' else 'prod')}"}
    if s.get("type", "lxc") == "lxc" and s.get("keyctl"):
        t.add("feat-keyctl")
    return t


def l1(rep, s, zones, res):
    h, zone, typ = s["hostname"], s["_zone"], s.get("type", "lxc")
    z = zones[zone]
    r = res.get(int(s["vmid"]))
    rep.ok(h, "L1", "exists", r is not None, f"vmid {s['vmid']}")
    if not r:
        return
    rep.ok(h, "L1", "type", r["type"] == ("qemu" if typ == "vm" else "lxc"), r["type"])
    rep.ok(h, "L1", "running", r.get("status") == "running", r.get("status"))
    rep.ok(h, "L1", "name", r.get("name") == h, r.get("name"))
    rep.ok(h, "L1", "pool", r.get("pool") == z.get("pool"), r.get("pool"))
    kind = "qemu" if typ == "vm" else "lxc"
    cfg = api(f"/nodes/{NODE}/{kind}/{s['vmid']}/config")
    tags = set(filter(None, re.split(r"[;, ]", cfg.get("tags", ""))))
    want = expected_tags(s, zone)
    rep.ok(h, "L1", "tags", tags == want, f"extra {sorted(tags - want)} missing {sorted(want - tags)}" if tags != want else "match")
    sz = {k: s.get(k, VM_DEFAULTS[k] if typ == "vm" else None) for k in VM_DEFAULTS}
    rep.ok(h, "L1", "cores", int(cfg.get("cores", 1)) == sz["cores"], f"{cfg.get('cores')} (spec {sz['cores']})")
    rep.ok(h, "L1", "memory", int(cfg.get("memory", 0)) == sz["memory_mb"], f"{cfg.get('memory')} MB (spec {sz['memory_mb']})")
    net = kv(cfg.get("net0"))
    rep.ok(h, "L1", "bridge+vlan", net.get("bridge") == z["bridge"] and net.get("tag") == str(z["vlan"]),
           f"{net.get('bridge')} tag {net.get('tag')} (zone {z['bridge']} tag {z['vlan']})")
    if typ == "lxc":
        disk = size_gb(cfg.get("rootfs"))
        ip = (net.get("ip") or "").split("/")[0]
        feats = kv(cfg.get("features"))
        if s.get("keyctl"):
            rep.ok(h, "L1", "keyctl", feats.get("keyctl") == "1", cfg.get("features", "none"))
    else:
        disk = size_gb(cfg.get("scsi0"))
        ip = (kv(cfg.get("ipconfig0")).get("ip") or "").split("/")[0]
        rep.ok(h, "L1", "machine q35", "q35" in cfg.get("machine", ""), cfg.get("machine"))
        try:
            ifs = api(f"/nodes/{NODE}/qemu/{s['vmid']}/agent/network-get-interfaces")["result"]
            seen = {a["ip-address"] for i in ifs for a in i.get("ip-addresses", [])}
            rep.ok(h, "L1", "guest agent IP", str(s["ip"]) in seen, f"agent sees {sorted(x for x in seen if ':' not in x)}")
        except Exception as e:  # agent down = fail (the module requires it)
            rep.add(h, "L1", "guest agent IP", "fail", f"agent not answering: {e}")
    rep.ok(h, "L1", "disk", disk == sz["disk_gb"], f"{disk} G (spec {sz['disk_gb']})")
    rep.ok(h, "L1", "ip", ip == str(s["ip"]), f"{ip} (spec {s['ip']})")


def l1_protected(rep, prot, res):
    # The pipeline tokens have NoAccess on protected guests, so they must be INVISIBLE here. Visible = the hard limit is
    # broken (an ACL change, or pool membership adding rights: incident 2026-10-05).
    seen = [str(v) for v in prot.get("vmids", {}) if int(v) in res]
    rep.ok("platform", "L1", "protected guests invisible to the pipeline token (NoAccess intact)", not seen,
           f"visible: {', '.join(seen)}" if seen else "100/300/303 not visible")


def ssh(ip, cmd):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new",
                           f"root@{ip}", cmd], capture_output=True, text=True, timeout=40)


def port_open(ip, port, t=5):
    try:
        with socket.create_connection((ip, port), timeout=t):
            return True
    except OSError:
        return False


def ping(ip):
    return subprocess.run(["ping", "-c", "1", "-W", "2", ip], capture_output=True).returncode == 0


def l2(rep, s):
    h, ip = s["hostname"], str(s["ip"])
    p = ping(ip)
    rep.add(h, "L2", "ping", "pass" if p else "skip", "reply" if p else "no ICMP reply (not fatal: firewall may drop ICMP)")
    rep.ok(h, "L2", "ssh port 22", port_open(ip, 22), ip)
    r = ssh(ip, "hostname")
    rep.ok(h, "L2", "ssh login + host key", r.returncode == 0, (r.stderr.strip().splitlines() or ["ok"])[-1])
    rep.ok(h, "L2", "hostname", r.stdout.strip() == h, r.stdout.strip() or "-")


def l3(rep, hosts, out):
    if not hosts:
        return
    os.makedirs(out, exist_ok=True)
    env = dict(os.environ, VERIFY_OUT=os.path.abspath(out), INFRA_ROOT=ROOT, LC_ALL="C.UTF-8",
               ANSIBLE_STDOUT_CALLBACK="default")
    p = subprocess.run(["ansible-playbook", "-i", "../scripts/inventory.py", "playbooks/verify.yml", "--limit", ",".join(hosts)],
                       cwd=os.path.join(ROOT, "ansible"), env=env, capture_output=True, text=True, timeout=900)
    open(os.path.join(out, "verify-ansible.log"), "w").write(p.stdout + p.stderr)
    for h in hosts:
        f = os.path.join(out, f"{h}.l3.json")
        if not os.path.exists(f):
            rep.add(h, "L3", "goss run", "fail", "no result (see verify-ansible.log)")
            continue
        try:
            d = json.load(open(f))
        except ValueError:
            rep.add(h, "L3", "goss run", "fail", "unreadable result")
            continue
        if "error" in d:
            rep.add(h, "L3", "goss run", "fail", d["error"])
            continue
        for r in sorted(d.get("results", []), key=lambda x: (x.get("resource-type", ""), x.get("resource-id", ""))):
            name = f"{r.get('resource-type', '')}:{r.get('resource-id', '')}:{r.get('property', '')}"
            st = "skip" if r.get("skipped") else "pass" if r.get("successful") else "fail"
            ev = r.get("summary-line", "") if st != "pass" else (r.get("found") or [""])[0] if isinstance(r.get("found"), list) else ""
            if st == "fail" and r.get("resource-id") == "l3-no-failed-units":
                try:  # Goss can't show the command's output; name the failed units from the runner
                    ip = str(next(s["ip"] for s in load_specs() if s["hostname"] == h))
                    ev = "failed units: " + (ssh(ip, "systemctl list-units --state=failed --no-legend --plain | awk '{print $1}' | paste -sd' '").stdout.strip() or "?")
                except Exception:
                    pass
            rep.add(h, "L3", name, st, ev)


def gone(rep, entries, res, spec_hosts):
    for e in entries:
        h, vmid, ip = (e.split(":") + ["", ""])[:3]
        rep.ok(h, "gone", "no guest", int(vmid) not in res, f"vmid {vmid}")
        if ip:
            rep.ok(h, "gone", "ip free", not ping(ip) and not port_open(ip, 22, 3), ip)
        rep.ok(h, "gone", "not in specs/inventory", h not in spec_hosts, "")


def render_md(rep):
    icon = {"pass": "✅", "fail": "❌", "skip": "⚠️"}
    lines = []
    for h in dict.fromkeys(r["host"] for r in rep.rows):
        rows = [r for r in rep.rows if r["host"] == h]
        f = sum(r["status"] == "fail" for r in rows)
        lines.append(f"<details{' open' if f else ''}><summary><b>{h}</b>: {'❌ ' + str(f) + ' failed' if f else '✅ all passed'} "
                     f"({len(rows)} checks)</summary>\n\n| Layer | Check | | Evidence |\n|---|---|---|---|")
        for r in rows:
            ev = r["evidence"].replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {r['layer']} | {r['check']} | {icon[r['status']]} | {ev} |")
        lines.append("\n</details>")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--standards", required=True)
    ap.add_argument("--hosts", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--gone", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-l3", action="store_true")
    a = ap.parse_args()
    zones = yaml.safe_load(open(os.path.join(a.standards, "naming/zones.yaml")))["zones"]
    prot = yaml.safe_load(open(os.path.join(a.standards, "protected.yaml"))) or {}
    specs = {s["hostname"]: s for s in load_specs()}
    want = sorted(specs) if a.all else [h for h in a.hosts.split(",") if h]
    rep = Report()
    unknown = [h for h in want if h not in specs]
    for h in unknown:
        rep.add(h, "L1", "spec exists", "fail", "no spec for this host")
    want = [h for h in want if h in specs]
    res = {int(r["vmid"]): r for r in api("/cluster/resources?type=vm")}
    l1_protected(rep, prot, res)
    reach = []
    for h in want:
        try:
            l1(rep, specs[h], zones, res)
        except Exception as e:
            rep.add(h, "L1", "platform read", "fail", e)
        l2(rep, specs[h])
        if not any(r["host"] == h and r["layer"] == "L2" and r["status"] == "fail" for r in rep.rows):
            reach.append(h)
    for h in sorted(set(want) - set(reach)):
        rep.add(h, "L3", "goss run", "fail", "skipped: not reachable over SSH (L2)")
    if not a.no_l3:
        l3(rep, reach, a.out)
    gone(rep, [e for e in a.gone.split(",") if e], res, set(specs))
    os.makedirs(a.out, exist_ok=True)
    json.dump({"rows": rep.rows, "failed": len(rep.failed)}, open(os.path.join(a.out, "verify.json"), "w"), indent=1)
    md = render_md(rep)
    open(os.path.join(a.out, "verify.md"), "w").write(md + "\n")
    print(md)
    sys.exit(1 if rep.failed else 0)


if __name__ == "__main__":
    main()
