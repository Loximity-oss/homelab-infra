#!/usr/bin/env python3
"""Guests in the saved plans of an apply, by action. Usage: plan_guests.py <plans_dir> <stack>...
Prints JSON {"create": [..], "update": [..], "replace": [..], "delete": [{"host","vmid","ip"}]}."""
import json, os, sys

GUEST = {"proxmox_virtual_environment_container", "proxmox_virtual_environment_vm"}


def ip_of(state):
    try:
        return (state["initialization"][0]["ip_config"][0]["ipv4"][0]["address"] or "").split("/")[0]
    except (KeyError, IndexError, TypeError):
        return ""


out = {"create": [], "update": [], "replace": [], "delete": []}
plans, stacks = sys.argv[1], sys.argv[2:]
for st in stacks:
    parts = st.rstrip("/").split("/")
    f = os.path.join(plans, f"{parts[-2]}-{parts[-1]}.plan.json")
    if not os.path.exists(f):
        continue
    for rc in json.load(open(f)).get("resource_changes", []):
        if rc.get("type") not in GUEST:
            continue
        acts, host = rc["change"]["actions"], rc.get("index")
        if "create" in acts and "delete" in acts:
            out["replace"].append(host)
        elif "create" in acts:
            out["create"].append(host)
        elif "delete" in acts:
            b = rc["change"].get("before") or {}
            out["delete"].append({"host": host, "vmid": b.get("vm_id"), "ip": ip_of(b)})
        elif "update" in acts:
            out["update"].append(host)
print(json.dumps(out))
