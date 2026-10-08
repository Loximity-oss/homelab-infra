#!/usr/bin/env python3
"""Refuse to apply when a Proxmox template is more than clone-only for the apply token (design-v7 decision 32, D5).
Checks the token's own effective rights (pool membership or a broad ACL would show up here).
Usage: PROXMOX_VE_API_TOKEN=... template-guard.py <standards_dir>"""
import json, os, ssl, sys, urllib.request
import yaml

ALLOWED = {"VM.Audit", "VM.Clone"}
std = sys.argv[1]
lo, hi = (yaml.safe_load(open(os.path.join(std, "protected.yaml"))) or {}).get("templates", [9000, 9199])
ctx = ssl._create_unverified_context()  # PVE API is self-signed until the internal PKI covers it (provider: insecure)


def api(path):
    req = urllib.request.Request("https://192.168.67.2:8006/api2/json" + path,
                                 headers={"Authorization": "PVEAPIToken=" + os.environ["PROXMOX_VE_API_TOKEN"]})
    return json.load(urllib.request.urlopen(req, context=ctx, timeout=20))["data"]


bad, seen = [], []
for r in api("/cluster/resources?type=vm"):
    vmid = int(r["vmid"])
    if lo <= vmid <= hi:
        seen.append(vmid)
        privs = set(api(f"/access/permissions?path=/vms/{vmid}").get(f"/vms/{vmid}", {}))
        extra = sorted(privs - ALLOWED)
        if extra:
            bad.append(f"/vms/{vmid}: {', '.join(extra)}")
if bad:
    print("templates writable by the apply token (expected only VM.Audit, VM.Clone):\n  " + "\n  ".join(bad))
    print("fix (root on the PVE host): pveum acl modify /vms/<id> --users svc-tf-apply@pve "
          "--tokens 'svc-tf-apply@pve!pipeline' --roles DevOpsTemplateUse")
    sys.exit(1)
print(f"template guard: ok ({len(seen)} template(s) clone-only: {sorted(seen)})")
