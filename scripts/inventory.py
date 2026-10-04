#!/usr/bin/env python3
"""Ansible dynamic inventory built from the guest specs (never hand-edited).
Groups: site_<site>, zone_<zone>, role_<role>, os_<os>, patch_<ring>, owner_<owner>."""
import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from specs import load_specs

inv = {"_meta": {"hostvars": {}}, "all": {"children": []}}
def add(group, host):
    inv.setdefault(group, {"hosts": []})["hosts"].append(host)
for s in load_specs():
    h = s["hostname"]
    inv["_meta"]["hostvars"][h] = {"ansible_host": s["ip"], "vmid": s["vmid"], "zone": s["_zone"], "role": s["role"]}
    for g in (f"site_{s['_site']}", f"zone_{s['_zone']}", f"role_{s['role']}", f"os_{s['os']}", f"patch_{s['patch']}", f"owner_{s['owner']}"):
        add(g, h)
inv["all"]["children"] = sorted(k for k in inv if k not in ("_meta", "all"))
print(json.dumps(inv, indent=1 if "--pretty" in sys.argv else None))
