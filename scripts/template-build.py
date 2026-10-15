#!/usr/bin/env python3
"""VM template builds from reviewed specs (homelab-standards PR #17, Faris decisions 2026-10-15; docs/templates.md).

Runs only in the `template` workflow (runner host-template, user runner-template) with the builder token
svc-tpl-build@pve!pipeline (env PVE_TOKEN). Proxmox API only: no shell on the PVE host, no download-url.
Enforced here AND by the token's per-VMID ACLs: VMIDs only in template_lifecycle.builder_ranges / scratch, never a
protected VMID, bridge vmbr1 only (vmbr0 never), never `purge` on delete (it drops the VMID's ACL entries).
The apply token stays clone-only on templates (scripts/template-guard.py, decision 32 D5 - unchanged).

Usage:
  template-build.py check <infra_dir> <standards_dir>                     validate every spec (PR check, offline)
  template-build.py build <infra_dir> <standards_dir> --report F (--diff A B | --spec PATH)
  template-build.py refresh-check <infra_dir> <standards_dir> --out DIR  upstream checksum moved? (no PVE access)
"""
import argparse, hashlib, json, os, pathlib, re, shlex, shutil, ssl, subprocess, sys, tempfile, time
import urllib.error, urllib.parse, urllib.request, uuid
import yaml

API = "https://192.168.67.2:8006/api2/json"
NODE = os.environ.get("PVE_NODE", "hm1-mgt-pve01")
BRIDGE = "vmbr1"
DATASTORES = {"lvmt-nvme01", "lvmt-sata01"}
DEFAULT_DS = "lvmt-nvme01"
DNS = "1.1.1.1 8.8.8.8"            # as terraform/modules/vm-guests dns_servers until hm1-shr-dns01/02 exist
DISK_GB = 8                        # template disk = vm-guests min_disk_gb = lint VM_MIN_DISK_GB
BASE_TEMPLATE = 9000               # nixos-build: the throwaway build VM is a full clone of the Ubuntu template
MARK = "template-build scratch"    # description marker; only VMs carrying it are ever cleaned from scratch
SPEC_DIR = "terraform/templates/hm1"
SPEC_RE = re.compile(r"^terraform/templates/hm1/[0-9]{4}-[a-z0-9-]+\.yaml$")
NAME_RE = re.compile(r"^tpl-[a-z0-9]+-[a-z0-9]+$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
HEX40, HEX64, FPR = re.compile(r"^[0-9a-f]{40}$"), re.compile(r"^[0-9a-f]{64}$"), re.compile(r"^[0-9A-F]{40}$")
TOP_KEYS = {"name", "vmid", "os", "description", "source", "settings", "seal", "seal_user", "provision", "refresh",
            "refresh_check"}
SOURCE_KEYS = {"image": {"kind", "url", "version", "sha256", "signature"},
               "nixos-build": {"kind", "version", "config", "nixpkgs_rev"}}
SETTING_KEYS = {"cores", "memory_mb", "datastore", "cpu"}
REFRESH = {"weekly", "monthly", "quarterly", "release"}
SEAL_STEPS = [  # hygiene before `template` (docs/apps/vm-templates.md); run after every spec's provision steps
    "cloud-init clean --logs --seed --machine-id 2>/dev/null || cloud-init clean --logs --seed",
    "truncate -s 0 /etc/machine-id; rm -f /var/lib/dbus/machine-id",
    "rm -f /etc/ssh/ssh_host_* /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys /root/.bash_history",
    "sync",
]
VERIFY = {  # on a throwaway clone that logs in like a real guest: root + key from the Proxmox cloud-init drive
    "cloud-init done": "cloud-init status --wait >/dev/null 2>&1; cloud-init status | grep -q 'status: done'",
    "root login by key": "test \"$(id -u)\" = 0",
    "python3 (Ansible)": "python3 -V",
    "sshd active": "systemctl is-active sshd >/dev/null 2>&1 || systemctl is-active ssh >/dev/null 2>&1",
    "qemu-guest-agent active": "systemctl is-active qemu-guest-agent",
    "disk grew on clone": "test \"$(df -B1 --output=size / | tail -1 | tr -d ' ')\" -gt 9663676416",
    "machine-id present": "test -s /etc/machine-id",
    "serial console": "grep -q 'console=ttyS0' /proc/cmdline",
}
SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR"]   # ephemeral VM + ephemeral key: nothing to pin
UA = {"User-Agent": "hm1-template-build"}


class Fail(Exception):
    pass


def load(p):
    with open(p) as f:
        return yaml.safe_load(f) or {}


def lifecycle(std):
    lc = load(pathlib.Path(std, "naming/vmid-ranges.yaml")).get("template_lifecycle") or {}
    if not lc.get("builder_ranges") or not lc.get("scratch"):
        raise Fail("homelab-standards naming/vmid-ranges.yaml has no template_lifecycle (standards PR #17)")
    return lc


def in_ranges(vmid, ranges):
    return any(lo <= vmid <= hi for lo, hi in ranges)


def protected(std):
    return {int(v) for v in load(pathlib.Path(std, "protected.yaml")).get("vmids", {})}


def spec_files(infra):
    return sorted(pathlib.Path(infra, SPEC_DIR).glob("*.yaml"))


# ---------------------------------------------------------------- spec validation (PR gate and before every build)
def check_spec(p, infra, std):
    rel = os.path.relpath(p, infra)
    errs = []
    def e(m):
        errs.append(f"{rel}: {m}")
    try:
        s = load(p)
    except yaml.YAMLError as x:
        return [f"{rel}: invalid YAML: {x}"]
    if not isinstance(s, dict):
        return [f"{rel}: spec must be a mapping"]
    lc = lifecycle(std)
    os_tags = load(pathlib.Path(std, "naming/tags.yaml"))["keys"]["os"]
    if not SPEC_RE.match(rel):
        e(f"specs live at {SPEC_DIR}/<vmid>-<name>.yaml")
    if "vmbr0" in pathlib.Path(p).read_text():
        e("vmbr0 is protected (management network); templates use vmbr1 only")
    if set(s) - TOP_KEYS:
        e(f"unknown keys {sorted(set(s) - TOP_KEYS)}")
    for k in ("name", "vmid", "os", "source", "seal", "refresh"):
        if k not in s:
            e(f"missing key '{k}'")
    name, vmid, os_ = s.get("name"), s.get("vmid"), s.get("os")
    if not isinstance(name, str) or not NAME_RE.match(name):
        e("name must be tpl-<os><version>-<variant> (naming-conventions 6.4)")
    if not isinstance(vmid, int):
        e("vmid must be an integer")
    else:
        if not in_ranges(vmid, lc["builder_ranges"]):
            e(f"vmid {vmid} outside template_lifecycle.builder_ranges {lc['builder_ranges']}")
        if vmid in protected(std):
            e(f"vmid {vmid} is protected")
        if pathlib.Path(p).stem != f"{vmid}-{name}":
            e(f"file name must be '{vmid}-{name}.yaml'")
    if os_ not in os_tags:
        e(f"os '{os_}' not in naming/tags.yaml {os_tags}")
    elif isinstance(name, str) and not name.startswith(f"tpl-{os_}"):
        e(f"name must start with 'tpl-{os_}'")
    src = s.get("source") if isinstance(s.get("source"), dict) else {}
    kind = src.get("kind")
    if kind not in SOURCE_KEYS:
        e("source.kind must be image or nixos-build")
    else:
        if set(src) - SOURCE_KEYS[kind]:
            e(f"unknown source keys {sorted(set(src) - SOURCE_KEYS[kind])}")
        if kind == "image":
            url = str(src.get("url") or "")
            if not url.startswith("https://") or re.search(r"[<>\s]|/latest/", url):
                e("source.url must be a pinned https URL (no placeholders, no /latest/)")
            if not HEX64.match(str(src.get("sha256") or "")):
                e("source.sha256 must be 64 lowercase hex (from the vendor's signed checksum file)")
            sig = src.get("signature")
            if sig is not None:
                if not isinstance(sig, dict) or set(sig) != {"url", "type", "key", "fingerprint"}:
                    e("source.signature needs exactly url, type, key, fingerprint")
                else:
                    if not str(sig["url"]).startswith("https://") or re.search(r"[<>\s]", str(sig["url"])):
                        e("source.signature.url must be a pinned https URL")
                    if sig["type"] not in ("detached", "clearsigned-checksum"):
                        e("source.signature.type must be detached or clearsigned-checksum")
                    if not str(sig["key"]).startswith("terraform/templates/keys/") or not pathlib.Path(infra, str(sig["key"])).is_file():
                        e("source.signature.key must be a file under terraform/templates/keys/")
                    if not FPR.match(str(sig["fingerprint"])):
                        e("source.signature.fingerprint must be 40 uppercase hex")
        else:
            cfg = str(src.get("config") or "")
            if not cfg.startswith("terraform/templates/") or not pathlib.Path(infra, cfg).is_file():
                e("source.config must be a file under terraform/templates/")
            if not HEX40.match(str(src.get("nixpkgs_rev") or "")):
                e("source.nixpkgs_rev must be a pinned 40-hex git commit")
    st = s.get("settings") or {}
    if not isinstance(st, dict) or set(st) - SETTING_KEYS:
        e(f"settings may only set {sorted(SETTING_KEYS)} (disk is fixed at {DISK_GB}G)")
    else:
        if "datastore" in st and st["datastore"] not in DATASTORES:
            e(f"settings.datastore must be one of {sorted(DATASTORES)}")
        for k in ("cores", "memory_mb"):
            if k in st and (not isinstance(st[k], int) or st[k] <= 0):
                e(f"settings.{k} must be a positive integer")
    if s.get("seal") not in ("ssh", "none"):
        e("seal must be ssh or none")
    if "seal_user" in s and (s.get("seal") != "ssh" or not USER_RE.match(str(s["seal_user"]))):
        e("seal_user needs seal: ssh and a plain user name")
    prov = s.get("provision") or []
    if not isinstance(prov, list) or not all(isinstance(x, str) for x in prov):
        e("provision must be a list of shell commands")
    elif prov and s.get("seal") != "ssh":
        e("provision needs seal: ssh")
    if s.get("refresh") not in REFRESH:
        e(f"refresh must be one of {sorted(REFRESH)}")
    rc = s.get("refresh_check")
    if rc is not None:
        if not isinstance(rc, dict) or set(rc) != {"checksum_url", "match"} or not str(rc.get("checksum_url")).startswith("https://"):
            e("refresh_check needs checksum_url (https) and match (regex)")
        else:
            try:
                re.compile(str(rc["match"]))
            except re.error as x:
                e(f"refresh_check.match is not a regex: {x}")
    return errs


def check_all(infra, std):
    files = spec_files(infra)
    if not files:
        return []
    try:
        lifecycle(std)
    except Fail as x:
        return [str(x)]
    errs, seen = [], {}
    for p in files:
        errs += check_spec(p, infra, std)
        try:
            s = load(p)
        except yaml.YAMLError:
            continue
        for k in ("vmid", "name"):
            v = s.get(k) if isinstance(s, dict) else None
            if v is not None:
                if (k, v) in seen:
                    errs.append(f"{os.path.relpath(p, infra)}: duplicate {k} '{v}' (also in {seen[(k, v)]})")
                seen[(k, v)] = p.name
    return errs


# ---------------------------------------------------------------- helpers
def run(cmd, timeout=600, env=None):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout, env=env)
    if r.returncode:
        raise Fail(f"{' '.join(map(str, cmd[:2]))}: rc={r.returncode} {(r.stderr or r.stdout)[-400:].strip()}")
    return r.stdout


def fetch(url, dest):
    """Vendor HTTPS download with system CA verification; returns sha256."""
    h = hashlib.sha256()
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            h.update(chunk)
            f.write(chunk)
    return h.hexdigest()


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def check_signature(src, infra, img, sha, work):
    sig = src.get("signature")
    if not sig:
        return "signature: none in spec (sha256 pin only)"
    home = work / "gnupg"
    home.mkdir(mode=0o700, exist_ok=True)
    env = {**os.environ, "GNUPGHOME": str(home)}
    key = pathlib.Path(infra, sig["key"])
    fprs = [l.split(":")[9] for l in run(["gpg", "--batch", "--show-keys", "--with-colons", key], env=env).splitlines()
            if l.startswith("fpr:")]
    if sig["fingerprint"] not in fprs:
        raise Fail(f"{sig['key']} does not contain key {sig['fingerprint']}")
    ring = work / "vendor.gpg"
    run(["gpg", "--batch", "--yes", "--dearmor", "--output", ring, key], env=env)
    sfile = work / "signature"
    fetch(sig["url"], sfile)
    if sig["type"] == "detached":
        run(["gpgv", "--keyring", ring, sfile, img], env=env)
    elif sha not in run(["gpgv", "--keyring", ring, "--output", "-", sfile], env=env).lower():
        raise Fail("sha256 not listed in the vendor's signed checksum file")
    return f"signature: ok ({sig['type']}, key ...{sig['fingerprint'][-16:]})"


def ssh(key, ip, cmd, user="root", timeout=1800):
    if user != "root":
        cmd = "sudo -n bash -c " + shlex.quote(cmd)
    return run(["ssh", "-i", key, *SSH_OPTS, f"{user}@{ip}", cmd], timeout=timeout)


def wait_ssh(key, ip, user="root", timeout=900):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if subprocess.run(["ssh", "-i", str(key), *SSH_OPTS, f"{user}@{ip}", "true"], capture_output=True,
                              timeout=30).returncode == 0:
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(10)
    raise Fail(f"no SSH as {user}@{ip} after {timeout}s (cloud-init? flow CT 300 -> stg :22?)")


def stg_net(std):
    z = load(pathlib.Path(std, "naming/zones.yaml"))["zones"]["stg"]
    ips = [ip for ip, what in (z.get("reserved_ips") or {}).items() if str(what).startswith("template-build")]
    if not ips:
        raise Fail("naming/zones.yaml stg has no reserved_ips entry 'template-build' (Network Engineer)")
    return {"ip": ips[0], "prefix": z["cidr"].split("/")[1], "gw": z["gateway"], "vlan": z["vlan"]}


# ---------------------------------------------------------------- Proxmox API (builder token)
class Pve:
    def __init__(self, token):
        self.h = {"Authorization": f"PVEAPIToken={token}"}
        self.ctx = ssl._create_unverified_context()  # PVE API self-signed until the internal PKI covers it (as template-guard)

    def call(self, method, path, data=None, timeout=120):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(API + path, data=body, method=method, headers=self.h)
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=timeout) as r:
                return json.load(r).get("data")
        except urllib.error.HTTPError as x:
            raise Fail(f"PVE {method} {path.split('?')[0]}: {x.code} {x.read().decode(errors='replace')[:300]}")

    def wait(self, upid, timeout=3600):
        if not (isinstance(upid, str) and upid.startswith("UPID:")):
            return
        q, end = urllib.parse.quote(upid, safe=""), time.time() + timeout
        while time.time() < end:
            st = self.call("GET", f"/nodes/{NODE}/tasks/{q}/status")
            if st.get("status") == "stopped":
                if st.get("exitstatus") != "OK":
                    raise Fail(f"task {upid} failed: {st.get('exitstatus')}")
                return
            time.sleep(3)
        raise Fail(f"task {upid} timed out")

    def do(self, method, path, data=None, timeout=3600):
        self.wait(self.call(method, path, data), timeout)

    def qm(self, vmid, sub=""):
        return f"/nodes/{NODE}/qemu/{vmid}{sub}"

    def vmids(self):
        return {int(r["vmid"]) for r in self.call("GET", "/cluster/resources?type=vm") or []}

    def config(self, vmid):
        return self.call("GET", self.qm(vmid, "/config")) or {}

    def status(self, vmid):
        return (self.call("GET", self.qm(vmid, "/status/current")) or {}).get("status")

    def content(self, store, kind):
        return self.call("GET", f"/nodes/{NODE}/storage/{store}/content?content={kind}") or []

    def upload(self, store, path, filename, sha):
        b = uuid.uuid4().hex
        # PVE rejects the upload (400) unless checksum-algorithm comes before checksum in the form (dry run 2026-10-15)
        fields = {"content": "import", "checksum-algorithm": "sha256", "checksum": sha}
        pre = "".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in fields.items())
        pre += (f'--{b}\r\nContent-Disposition: form-data; name="filename"; filename="{filename}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n")
        pre, post = pre.encode(), f"\r\n--{b}--\r\n".encode()
        def body():
            yield pre
            with open(path, "rb") as f:
                while chunk := f.read(1 << 20):
                    yield chunk
            yield post
        req = urllib.request.Request(API + f"/nodes/{NODE}/storage/{store}/upload", data=body(), method="POST",
                                     headers={**self.h, "Content-Type": f"multipart/form-data; boundary={b}",
                                              "Content-Length": str(len(pre) + os.path.getsize(path) + len(post))})
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=3600) as r:
                self.wait(json.load(r).get("data"))
        except urllib.error.HTTPError as x:
            raise Fail(f"PVE upload: {x.code} {x.read().decode(errors='replace')[:300]}")


def destroy(pve, vmid):
    if pve.status(vmid) == "running":
        pve.do("POST", pve.qm(vmid, "/status/stop"))
    # PVE drops the VMID's ACL entries on every destroy, purge or not (dry run 2026-10-15); pve-feature-broker
    # (root, every minute) re-applies them. Anything that reuses this VMID first calls wait_alloc().
    pve.do("DELETE", pve.qm(vmid, "?destroy-unreferenced-disks=1"))


def wait_alloc(pve, vmid, timeout=300):
    end = time.time() + timeout
    while time.time() < end:
        if (pve.call("GET", f"/access/permissions?path=/vms/{vmid}") or {}).get(f"/vms/{vmid}", {}).get("VM.Allocate"):
            return
        time.sleep(10)
    raise Fail(f"no VM.Allocate on /vms/{vmid} after {timeout}s: pve-feature-broker has not re-applied the template "
               "ACLs (root: systemctl status pve-feature-broker.timer)")


def clean_scratch(pve, lc):
    lo, hi = lc["scratch"]
    for v in sorted(pve.vmids()):
        if lo <= v <= hi:
            if MARK not in (pve.config(v).get("description") or ""):
                raise Fail(f"scratch VMID {v} exists without the '{MARK}' marker; not touching it")
            destroy(pve, v)


def boot_cfg(pve, vmid, net, pub, user="root"):
    pve.call("PUT", pve.qm(vmid, "/config"), {
        "ciuser": user, "sshkeys": urllib.parse.quote(pub.strip(), safe=""),
        "ipconfig0": f"ip={net['ip']}/{net['prefix']},gw={net['gw']}", "nameserver": DNS,
        "net0": f"virtio,bridge={BRIDGE},tag={net['vlan']}"})


def unboot_cfg(pve, vmid):
    # net0 keeps bridge vmbr1 + the stg VLAN tag: untagged vmbr1 would need SDN.Use on the whole bridge (all VLANs),
    # and every guest apply sets its own NIC/VLAN anyway.
    pve.call("PUT", pve.qm(vmid, "/config"), {"delete": "ciuser,sshkeys,ipconfig0,nameserver"})


def disk_bytes(val):
    m = re.search(r"size=(\d+)([KMGT]?)", val or "")
    return int(m[1]) * {"": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}[m[2]] if m else 0


def seal_and_poweroff(pve, vmid, key, ip, user):
    """SEAL_STEPS and the poweroff in ONE ssh session: the seal removes the build key (authorized_keys), so no new
    login is possible after it (template run 224: `sync` as its own login got Permission denied)."""
    ssh(key, ip, " && ".join(f"( {c} )" for c in SEAL_STEPS)
        + " && ( nohup sh -c 'sleep 2; systemctl poweroff' >/dev/null 2>&1 & )", user, 600)
    end = time.time() + 300
    while time.time() < end:
        if pve.status(vmid) == "stopped":
            return
        time.sleep(5)
    raise Fail(f"VM {vmid} did not power off cleanly after sealing; not templating an unclean disk")


# ---------------------------------------------------------------- build steps
def nixos_image(pve, spec, infra, lc, net, key, pub, work, report):
    """NixOS qcow2 from nixpkgs' own image builder (config.system.build.images.qemu) at the pinned nixpkgs_rev,
    built in a throwaway stg VM cloned from 9000 (nix-community/nixos-generators is archived since 2026-01-30)."""
    src, b = spec["source"], lc["scratch"][0]
    ds = (spec.get("settings") or {}).get("datastore", DEFAULT_DS)
    wait_alloc(pve, b)
    pve.do("POST", pve.qm(BASE_TEMPLATE, "/clone"), {"newid": b, "full": 1, "name": "tplbuild-nixos", "storage": ds,
                                                    "description": MARK})
    # cpu host: nixpkgs images.qemu builds the disk inside a VM and needs /dev/kvm (nested KVM is on: kvm_intel
    # nested=Y). Only this throwaway build VM; candidates and templates keep x86-64-v2-AES (template run 231).
    pve.call("PUT", pve.qm(b, "/config"), {"cores": 4, "memory": 8192, "cpu": "host"})
    boot_cfg(pve, b, net, pub)
    pve.do("PUT", pve.qm(b, "/resize"), {"disk": "scsi0", "size": "40G"})
    pve.do("POST", pve.qm(b, "/status/start"))
    ip = net["ip"]
    wait_ssh(key, ip)
    run(["scp", "-i", key, *SSH_OPTS, pathlib.Path(infra, src["config"]), f"root@{ip}:/root/configuration.nix"])
    ssh(key, ip, "cloud-init status --wait >/dev/null 2>&1; export DEBIAN_FRONTEND=noninteractive; "
                 "apt-get update -q && apt-get install -yq nix-bin", timeout=1800)
    ssh(key, ip, "nix-build '<nixpkgs/nixos>' -A config.system.build.images.qemu "
                 f"-I nixpkgs=https://github.com/NixOS/nixpkgs/archive/{src['nixpkgs_rev']}.tar.gz "
                 "-I nixos-config=/root/configuration.nix -o /root/out", timeout=7200)
    ssh(key, ip, "cp -L \"$(find -L /root/out -name '*.qcow2' | head -1)\" /root/nixos.qcow2", timeout=600)
    img = work / "nixos.qcow2"
    run(["scp", "-i", key, *SSH_OPTS, f"root@{ip}:/root/nixos.qcow2", img], timeout=3600)
    destroy(pve, b)
    sha = sha256_file(img)
    report(f"nixos image built in stg VM {b}: nixpkgs {src['nixpkgs_rev'][:12]} (images.qemu), sha256 {sha}")
    return img, sha


def verify(pve, cand, vfy, ds, net, key, pub, report):
    wait_alloc(pve, vfy)
    pve.do("POST", pve.qm(cand, "/clone"), {"newid": vfy, "full": 1, "name": "tplverify", "storage": ds,
                                           "description": MARK})
    boot_cfg(pve, vfy, net, pub)
    pve.do("PUT", pve.qm(vfy, "/resize"), {"disk": "scsi0", "size": "+4G"})
    pve.do("POST", pve.qm(vfy, "/status/start"))
    wait_ssh(key, net["ip"])
    bad = []
    for name, cmd in VERIFY.items():
        try:
            ssh(key, net["ip"], cmd, timeout=900)
            report(f"verify ✅ {name}")
        except (Fail, subprocess.TimeoutExpired) as x:
            bad.append(name)
            report(f"verify ❌ {name}: {str(x)[:200]}")
    try:
        ifs = json.dumps(pve.call("GET", pve.qm(vfy, "/agent/network-get-interfaces")))
        ok = net["ip"] in ifs
    except Fail as x:
        ok, ifs = False, str(x)
    report(f"verify {'✅' if ok else '❌'} guest agent reports {net['ip']}")
    if not ok:
        bad.append("guest agent IP")
    destroy(pve, vfy)
    if bad:
        raise Fail(f"verify clone failed ({', '.join(bad)}); the live template was not touched")


def backup(pve, vmid, store, report):
    pve.do("POST", f"/nodes/{NODE}/vzdump", {"vmid": vmid, "storage": store, "mode": "stop", "compress": "zstd",
                                             "notes-template": "{{guestname}} {{vmid}} template pre-replace"})
    items = sorted((c for c in pve.content(store, "backup") if str(c.get("vmid")) == str(vmid)),
                   key=lambda c: c.get("ctime", 0))
    if not items:
        raise Fail(f"pre-change backup of {vmid} not found; nothing replaced")
    report(f"backup of the old template: `{items[-1]['volid']}`")
    return items[-1]["volid"]


def replace(pve, cand, target, spec, ds, desc, store, report):
    old = None
    if target in pve.vmids():
        old = backup(pve, target, store, report)
        destroy(pve, target)
    try:
        wait_alloc(pve, target)
        pve.do("POST", pve.qm(cand, "/clone"), {"newid": target, "full": 1, "name": spec["name"], "storage": ds,
                                               "description": desc})
        pve.do("POST", pve.qm(target, "/template"))
    except Fail as x:
        if old:
            report(f"⚠️ replace failed ({x}); restoring {target} from `{old}`")
            if target in pve.vmids():
                destroy(pve, target)
            try:
                wait_alloc(pve, target)
                pve.do("POST", f"/nodes/{NODE}/qemu", {"vmid": target, "archive": old, "storage": ds})
            except Fail as y:
                report(f"❌ restore failed ({y}). Root: `qmrestore {old} {target} --storage {ds} && qm template {target}`")
                raise
            report(f"restored {target} from `{old}`")
        raise


def set_tags(pve, vmid, os_, mgd):
    tags = f"os-{os_};mgd-{mgd};owner-infra"
    try:
        pve.call("PUT", pve.qm(vmid, "/config"), {"tags": tags})
        return f"tags: {tags}"
    except Fail as x:
        return (f"⚠️ tags not set ({str(x)[:120]}): registered tags need Sys.Modify on /. "
                f"Root one-off: `qm set {vmid} --tags '{tags}'`")


def prune(pve, vmid, store, report, keep=2):
    """Best effort: deleting volumes needs Datastore.Allocate, which the builder does not get (it would also allow
    editing the storage itself). pve-feature-broker prunes as root to the same keep count."""
    for kind in ("import", "backup"):
        mine = [c for c in pve.content(store, kind)
                if (c["volid"].split("/")[-1].startswith(f"{vmid}-") if kind == "import" else str(c.get("vmid")) == str(vmid))]
        for c in sorted(mine, key=lambda c: c.get("ctime", 0))[:-keep]:
            try:
                pve.do("DELETE", f"/nodes/{NODE}/storage/{store}/content/{urllib.parse.quote(c['volid'], safe='')}")
            except Fail:
                report(f"prune: `{c['volid']}` left for pve-feature-broker (root)")
                return


def guard_vmids(std, lc, *vmids):
    prot, lo, hi = protected(std), *lc["scratch"]
    for v in vmids:  # belt and braces: the token's per-VMID ACLs enforce the same
        if v in prot or not (in_ranges(v, lc["builder_ranges"]) or lo <= v <= hi):
            raise Fail(f"refusing VMID {v} (outside builder_ranges/scratch or protected)")


def build_one(pve, rel, infra, std, lc, report):
    path = pathlib.Path(infra, rel)
    errs = check_spec(path, infra, std)
    if errs:
        raise Fail("spec invalid: " + "; ".join(errs))
    spec = load(path)
    target, src = spec["vmid"], spec["source"]
    ds = (spec.get("settings") or {}).get("datastore", DEFAULT_DS)
    st = spec.get("settings") or {}
    store = lc.get("import_storage", "dir-import01")
    lo = lc["scratch"][0]
    cand, vfy = lo + 1, lo + 2
    guard_vmids(std, lc, target, lo, cand, vfy)
    # Replacing an existing template destroys it first, and PVE drops its per-VMID ACLs on destroy; until root
    # re-applies them automatically (pve-feature-broker follow-up, Faris decision) a replace could lose the template.
    # New templates only; set TPL_ALLOW_REPLACE=1 in the workflow once that lands.
    if target in pve.vmids() and os.environ.get("TPL_ALLOW_REPLACE") != "1":
        raise Fail(f"template {target} exists and replace is disabled until the ACL re-apply lands; nothing changed")
    net = stg_net(std)
    work = pathlib.Path(tempfile.mkdtemp(prefix="tpl-", dir=os.environ.get("TPL_WORK", "/var/tmp")))
    try:
        key = work / "id_ed25519"
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"template-build-{target}", "-f", key])
        pub = (work / "id_ed25519.pub").read_text()
        clean_scratch(pve, lc)
        if src["kind"] == "image":
            sha = src["sha256"]
            vol = f"{target}-{sha[:16]}.qcow2"
            if f"{store}:import/{vol}" in {c["volid"] for c in pve.content(store, "import")}:
                report(f"image: cached `{store}:import/{vol}` (checksum-verified at upload)")
            else:
                img = work / "image.qcow2"
                got = fetch(src["url"], img)
                if got != sha:
                    raise Fail(f"sha256 mismatch: spec {sha}, downloaded {got}")
                report(f"image: {src['url']} sha256 ok")
                report(check_signature(src, infra, img, sha, work))
                pve.upload(store, img, vol, sha)
        else:
            img, sha = nixos_image(pve, spec, infra, lc, net, key, pub, work, report)
            vol = f"{target}-{sha[:16]}.qcow2"
            pve.upload(store, img, vol, sha)
        wait_alloc(pve, cand)
        desc = (f"{spec.get('description') or spec['name']}\nBuilt by the homelab-infra template pipeline from "
                f"{rel} @ {os.environ.get('SHA', '?')[:12]}; image sha256 {sha}; {time.strftime('%Y-%m-%d')}; "
                "svc-tpl-build. Clone-only for the apply token (decision 32 D5).")
        pve.do("POST", f"/nodes/{NODE}/qemu", {
            "vmid": cand, "name": f"{spec['name']}-cand", "description": f"{MARK}\n{desc}", "ostype": "l26",
            "machine": "q35", "cores": st.get("cores", 2), "memory": st.get("memory_mb", 2048),
            "cpu": st.get("cpu", "x86-64-v2-AES"), "scsihw": "virtio-scsi-single",
            "agent": "enabled=1,fstrim_cloned_disks=1", "serial0": "socket", "vga": "serial0",
            "net0": f"virtio,bridge={BRIDGE},tag={net['vlan']}", "ide2": f"{ds}:cloudinit", "boot": "order=scsi0",
            "scsi0": f"{ds}:0,import-from={store}:import/{vol},discard=on,iothread=1,ssd=1"})
        size = disk_bytes(pve.config(cand).get("scsi0"))
        if size > DISK_GB << 30:
            raise Fail(f"image disk is {size >> 30} GiB, larger than the {DISK_GB}G template disk (min_disk_gb)")
        if size < DISK_GB << 30:
            pve.do("PUT", pve.qm(cand, "/resize"), {"disk": "scsi0", "size": f"{DISK_GB}G"})
        if spec["seal"] == "ssh":
            user = spec.get("seal_user", "root")
            boot_cfg(pve, cand, net, pub, user)
            pve.do("POST", pve.qm(cand, "/status/start"))
            wait_ssh(key, net["ip"], user)
            ssh(key, net["ip"], "cloud-init status --wait >/dev/null 2>&1; true", user, 900)
            for cmd in spec.get("provision") or []:
                ssh(key, net["ip"], cmd, user)
            seal_and_poweroff(pve, cand, key, net["ip"], user)
            unboot_cfg(pve, cand)
            report(f"sealed: {len(spec.get('provision') or [])} provision step(s) + hygiene, clean poweroff")
        pve.do("POST", pve.qm(cand, "/template"))
        verify(pve, cand, vfy, ds, net, key, pub, report)
        replace(pve, cand, target, spec, ds, desc, store, report)
        report(set_tags(pve, target, spec["os"], lc.get("mgd_tag", "tpl")))
        destroy(pve, cand)
        prune(pve, target, store, report)
    finally:
        try:
            clean_scratch(pve, lc)
        except Exception as x:  # report, never mask the original error
            report(f"⚠️ scratch cleanup: {x}")
        shutil.rmtree(work, ignore_errors=True)


def retire_one(pve, rel, std, lc, report):
    vmid = int(pathlib.Path(rel).name.split("-", 1)[0])
    guard_vmids(std, lc, vmid)
    if vmid not in pve.vmids():
        report(f"{vmid}: already absent")
        return
    backup(pve, vmid, lc.get("import_storage", "dir-import01"), report)
    destroy(pve, vmid)
    report(f"retired template {vmid} (backup kept)")


def changed_specs(infra, a, b):
    out = run(["git", "-C", infra, "diff", "--name-status", "--no-renames", a, b, "--", "terraform/templates/"])
    build, retire, support = set(), set(), []
    for line in out.splitlines():
        st, f = line.split("\t", 1)
        if SPEC_RE.match(f):
            (retire if st.startswith("D") else build).add(f)
        else:
            support.append(f)
    for p in spec_files(infra):  # vendor key or nixos config changed -> rebuild the specs that reference it
        if any(s in p.read_text() for s in support):
            build.add(os.path.relpath(p, infra))
    return sorted(build), sorted(retire)


def refresh_check(infra, out):
    os.makedirs(out, exist_ok=True)
    for p in spec_files(infra):
        s = load(p)
        rc, src = s.get("refresh_check"), s.get("source") or {}
        if not rc or src.get("kind") != "image":
            continue
        try:
            with urllib.request.urlopen(urllib.request.Request(rc["checksum_url"], headers=UA), timeout=60) as r:
                text = r.read().decode(errors="replace")
        except (urllib.error.URLError, OSError) as x:
            pathlib.Path(out, f"{s['name']}.md").write_text(f"Refresh check for `{p.name}` failed: {x}\n")
            print(s["name"])
            continue
        found = {}
        for line in text.splitlines():
            m = (re.search(r"SHA256 \((?P<f>[^)]+)\) = (?P<h>[0-9a-f]{64})", line)
                 or re.match(r"^(?P<h>[0-9a-f]{64})\s+\*?(?P<f>\S+)$", line.strip()))
            if m and re.search(rc["match"], m["f"]):
                found[m["f"]] = m["h"]
        if found and src.get("sha256") in found.values():
            continue
        body = (f"`{p.name}` pins sha256 `{src.get('sha256')}`; upstream `{rc['checksum_url']}` lists:\n\n"
                + ("\n".join(f"- `{f}` `{h}`" for f, h in sorted(found.items())) or f"- nothing matching `{rc['match']}`")
                + "\n\nIf this is a newer image: open a minor-refresh PR (source.url/version/sha256 only, Class B).\n")
        pathlib.Path(out, f"{s['name']}.md").write_text(body)
        print(s["name"])
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["check", "build", "refresh-check"])
    ap.add_argument("infra")
    ap.add_argument("standards")
    ap.add_argument("--report")
    ap.add_argument("--diff", nargs=2)
    ap.add_argument("--spec")
    ap.add_argument("--out", default="refresh")
    a = ap.parse_args()
    if a.mode == "check":
        errs = check_all(a.infra, a.standards)
        print("\n".join(errs) if errs else f"templates: {len(spec_files(a.infra))} spec(s) ok")
        return 1 if errs else 0
    if a.mode == "refresh-check":
        return refresh_check(a.infra, a.out)
    lines, rc = [], 0
    def report(m):
        lines.append(f"- {m}")
        print(m, flush=True)
    try:
        lc = lifecycle(a.standards)
        pve = Pve(os.environ["PVE_TOKEN"])
        if a.spec:
            if not SPEC_RE.match(a.spec) or not pathlib.Path(a.infra, a.spec).is_file():
                raise Fail(f"--spec must be an existing {SPEC_DIR}/<vmid>-<name>.yaml")
            build, retire = [a.spec], []
        else:
            build, retire = changed_specs(a.infra, *a.diff)
        if not build and not retire:
            report("no template specs changed")
        for f in retire:
            report(f"**retire** `{f}`")
            try:
                retire_one(pve, f, a.standards, lc, report)
            except (Fail, OSError, subprocess.SubprocessError) as x:
                rc = 1
                report(f"❌ {f}: {x}")
        for f in build:
            report(f"**build** `{f}`")
            try:
                build_one(pve, f, a.infra, a.standards, lc, report)
                report(f"✅ `{f}`: built, verified, live")
            except (Fail, OSError, subprocess.SubprocessError, urllib.error.URLError) as x:
                rc = 1
                report(f"❌ `{f}`: {x} (the live template is unchanged unless a restore is reported above)")
    except Fail as x:
        rc = 1
        report(f"❌ {x}")
    if a.report:
        pathlib.Path(a.report).write_text(f"## {'✅' if rc == 0 else '❌'} Template build\n\n" + "\n".join(lines) + "\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
