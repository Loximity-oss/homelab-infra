#!/usr/bin/env python3
"""Change class from the plans and the changed paths (computed, never declared).
Usage: classify.py --changed changed.json --plans DIR --protected protected.yaml
Prints JSON {"class": "A|B|C", "reasons": [...], "summary": {...}}.

C (Faris): any delete/replace; protected VMIDs/bridges; pipeline/policy/secrets/keys paths; UniFi.
B (Reviewer): modules, Ansible roles/playbooks, imports, updates to existing shr guests.
A (auto): everything else (new guests, updates outside shr, docs).
"""
import argparse, fnmatch, json, pathlib, yaml

C_PATHS = [".gitea/*", "policy/*", "scripts/*", "secrets/*", ".sops.yaml", "ansible/keys/*", "terraform/stacks/*/unifi/*"]
B_PATHS = ["terraform/modules/*", "ansible/roles/*", "ansible/playbooks/*", "ansible/ansible.cfg"]

ap = argparse.ArgumentParser()
ap.add_argument("--changed", required=True)
ap.add_argument("--plans", required=True)
ap.add_argument("--protected", required=True)
a = ap.parse_args()
changed = json.load(open(a.changed))
prot = yaml.safe_load(open(a.protected)) or {}
pvmids = {int(v) for v in prot.get("vmids", {})}
pbridges = set(prot.get("bridges", []))

reasons = {"A": [], "B": [], "C": []}
summary = {"create": 0, "update": 0, "delete": 0, "replace": 0, "import": 0}

for f in changed["files"]:
    if any(fnmatch.fnmatch(f, p) for p in C_PATHS):
        reasons["C"].append(f"foundational path: {f}")
    elif any(fnmatch.fnmatch(f, p) for p in B_PATHS):
        reasons["B"].append(f"shared code: {f}")

for pf in sorted(pathlib.Path(a.plans).glob("*.plan.json")):
    plan = json.load(open(pf))
    stack = pf.name[: -len(".plan.json")]
    for rc in plan.get("resource_changes", []):
        acts = rc["change"]["actions"]
        after = rc["change"].get("after") or {}
        before = rc["change"].get("before") or {}
        addr = f"{stack}:{rc['address']}"
        if rc["change"].get("importing"):
            summary["import"] += 1; reasons["B"].append(f"import: {addr}")
        if acts == ["no-op"] or acts == ["read"]:
            continue
        if "delete" in acts and "create" in acts:
            summary["replace"] += 1; reasons["C"].append(f"replace: {addr}")
        elif "delete" in acts:
            summary["delete"] += 1; reasons["C"].append(f"destroy: {addr}")
        elif "create" in acts:
            summary["create"] += 1; reasons["A"].append(f"create: {addr}")
        elif "update" in acts:
            summary["update"] += 1
            if "/shr" in stack or stack.endswith("shr"):
                reasons["B"].append(f"update shared service: {addr}")
            else:
                reasons["A"].append(f"update: {addr}")
        for side in (before, after):
            vmid = side.get("vm_id")
            if vmid is not None and int(vmid) in pvmids:
                reasons["C"].append(f"protected VMID {vmid}: {addr}")
            for nic in side.get("network_interface") or []:
                if nic.get("bridge") in pbridges:
                    reasons["C"].append(f"protected bridge {nic.get('bridge')}: {addr}")

cls = "C" if reasons["C"] else "B" if reasons["B"] else "A"
print(json.dumps({"class": cls, "reasons": reasons[cls] or ["routine change"], "all_reasons": reasons, "summary": summary}))
