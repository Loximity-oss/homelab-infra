#!/usr/bin/env python3
"""Refuse a template build unless the builder token (svc-tpl-build@pve!pipeline) is exactly as narrow as
homelab-standards PR #17 says. Checks the token's own effective rights, like template-guard.py does for the apply
token (which stays clone-only on templates; that guard is unchanged and still runs before every guest apply).
Usage: PROXMOX_VE_API_TOKEN=<builder token> template-builder-guard.py <standards_dir>"""
import json, os, re, ssl, sys, urllib.parse, urllib.request
import yaml

NODE = os.environ.get("PVE_NODE", "hm1-mgt-pve01")
VM_PRIVS = {"VM.Allocate", "VM.Audit", "VM.Clone", "VM.Config.Disk", "VM.Config.CDROM", "VM.Config.Cloudinit",
            "VM.Config.CPU", "VM.Config.Memory", "VM.Config.Network", "VM.Config.HWType", "VM.Config.Options",
            "VM.PowerMgmt", "VM.GuestAgent.Audit", "VM.Backup"}          # role DevOpsTemplateBuild
IMPORT_PRIVS = {"Datastore.AllocateTemplate", "Datastore.AllocateSpace", "Datastore.Audit"}
DS_PRIVS = {"Datastore.AllocateSpace", "Datastore.Audit"}
DATASTORES = {"lvmt-nvme01", "lvmt-sata01"}
SDN_OK = re.compile(r"^/sdn/zones/localnetwork/vmbr1(/60)?$")
NOTHING = ["/", "/nodes", f"/nodes/{NODE}", "/access", "/pool", "/storage", "/sdn", "/mapping"]

std = sys.argv[1]
lc = (yaml.safe_load(open(os.path.join(std, "naming/vmid-ranges.yaml"))) or {}).get("template_lifecycle") or {}
prot = yaml.safe_load(open(os.path.join(std, "protected.yaml"))) or {}
if not lc.get("builder_ranges") or not lc.get("scratch"):
    sys.exit("builder guard: homelab-standards has no template_lifecycle (PR #17); nothing built")
ranges = [tuple(r) for r in lc["builder_ranges"]] + [tuple(lc["scratch"])]
store = lc.get("import_storage", "dir-import01")
pvmids = {int(v) for v in prot.get("vmids", {})}
ctx = ssl._create_unverified_context()  # PVE API is self-signed until the internal PKI covers it (as template-guard)


def api(path):
    req = urllib.request.Request("https://192.168.67.2:8006/api2/json" + path,
                                 headers={"Authorization": "PVEAPIToken=" + os.environ["PROXMOX_VE_API_TOKEN"]})
    return json.load(urllib.request.urlopen(req, context=ctx, timeout=20))["data"]


def perms(path):
    return set((api("/access/permissions?path=" + urllib.parse.quote(path, safe="")) or {}).get(path, {}))


def ok_vm(v):
    return v not in pvmids and any(lo <= v <= hi for lo, hi in ranges)


bad = []
for path, privs in sorted((api("/access/permissions") or {}).items()):
    p = set(privs or {})
    if not p:
        continue
    m = re.fullmatch(r"/vms/(\d+)", path)
    if m:
        if not ok_vm(int(m[1])):
            bad.append(f"{path}: {sorted(p)} (outside builder_ranges/scratch, or protected)")
        elif p - VM_PRIVS:
            bad.append(f"{path}: extra {sorted(p - VM_PRIVS)}")
    elif path == f"/storage/{store}":
        if p - IMPORT_PRIVS:
            bad.append(f"{path}: extra {sorted(p - IMPORT_PRIVS)}")
    elif path.startswith("/storage/") and path.split("/")[2] in DATASTORES:
        if p - DS_PRIVS:
            bad.append(f"{path}: extra {sorted(p - DS_PRIVS)}")
    elif SDN_OK.match(path):
        if p - {"SDN.Use", "SDN.Audit"}:
            bad.append(f"{path}: extra {sorted(p - {'SDN.Use', 'SDN.Audit'})}")
    else:
        bad.append(f"{path}: {sorted(p)} (the builder must have nothing here)")
# Pool membership adds rights on top of path ACLs (incident 2026-10-05): any guest visible outside the ranges is a leak.
for r in api("/cluster/resources?type=vm") or []:
    if not ok_vm(int(r["vmid"])):
        bad.append(f"/vms/{r['vmid']} ({r.get('name')}): visible to the builder")
for v in sorted(pvmids):
    if perms(f"/vms/{v}"):
        bad.append(f"/vms/{v}: protected guest has privileges for the builder")
for br in prot.get("bridges", []):
    if "SDN.Use" in perms(f"/sdn/zones/localnetwork/{br}"):
        bad.append(f"/sdn/zones/localnetwork/{br}: SDN.Use on a protected bridge")
for path in NOTHING:
    p = perms(path)
    if p:
        bad.append(f"{path}: {sorted(p)} (the builder must have nothing here)")
if bad:
    print("builder token is wider than homelab-standards PR #17 allows:\n  " + "\n  ".join(sorted(set(bad))))
    print("fix (root on the PVE host): remove the extra ACLs / pool memberships for svc-tpl-build@pve and its token")
    sys.exit(1)
print(f"builder guard: ok (VMIDs {ranges}, storage {store}, SDN vmbr1 only)")
