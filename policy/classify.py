#!/usr/bin/env python3
"""Change class from the plans and the changed paths (computed, never declared).
Usage: classify.py --changed changed.json --plans DIR --protected protected.yaml [--base-dir MAIN --head-dir HEAD]
Prints JSON {"class": "A|B|C", "reasons": [...], "summary": {...}}.

C (Faris): any delete/replace; tests/*; template VMIDs; any non-guest (host-level) resource; protected VMIDs/bridges; pipeline/policy/keys paths; UniFi;
           stack code (terraform/stacks/**/*.tf: adding, changing or deleting a stack);
           template specs (terraform/templates/**) except a minor refresh;
           patches/* (monthly Sev3/4 patch bundles: Faris approves each one, docs/patching-pipeline.md).
B (Reviewer): modules, Ansible roles/playbooks, imports, updates to existing shr guests;
           minor template refresh (Faris decision 3, standards PR #17; see template_change).
A (auto): everything else (new guests, updates outside shr, docs).
"""
import argparse, fnmatch, json, pathlib, re, urllib.parse, yaml

# terraform/stacks/*.tf = stack wiring (backend, providers, module call). Adding or deleting a stack adds or removes a
# zone, and a deleted stack produces no plan at all (changed.py skips it), so it can never be judged from plans.
# patches/*: a merged bundle is applied on the last Saturday only with Faris's approval (scripts/ci-patch.sh monthly).
C_PATHS = ["host/*", ".gitea/*", "policy/*", "scripts/*", "secrets/*", ".sops.yaml", "ansible/keys/*", "terraform/stacks/*/unifi/*",
           "terraform/stacks/*.tf", "tests/*", "patches/*"]   # tests/*: the guest test contract (decision 32 D2)
B_PATHS = ["terraform/modules/*", "ansible/roles/*", "ansible/playbooks/*", "ansible/ansible.cfg"]
# Only guest resources can be A/B. Anything else the pipeline can touch with DevOpsAdmin (pools, cluster options,
# storage, network, SDN, firewall, ACLs, files on the host, ...) is host-level and therefore Class C.
GUEST_TYPES = {"proxmox_virtual_environment_container", "proxmox_virtual_environment_vm"}
# Template specs (built by the `template` workflow after merge). Minor refresh = only these leaves change.
TEMPLATE_SPEC = re.compile(r"^terraform/templates/[a-z0-9]+/[0-9]{4}-[a-z0-9-]+\.yaml$")
REFRESH_LEAVES = {("source", "url"), ("source", "sha256"), ("source", "version"), ("source", "signature", "url"),
                  ("source", "nixpkgs_rev")}
MISSING = object()

ap = argparse.ArgumentParser()
ap.add_argument("--changed", required=True)
ap.add_argument("--plans", required=True)
ap.add_argument("--protected", required=True)
ap.add_argument("--base-dir", default="")   # main checkout; needed to judge template specs (else they are C)
ap.add_argument("--head-dir", default="")   # PR head checkout
a = ap.parse_args()
changed = json.load(open(a.changed))
prot = yaml.safe_load(open(a.protected)) or {}
pvmids = {int(v) for v in prot.get("vmids", {})}
pbridges = set(prot.get("bridges", []))
tlo, thi = prot.get("templates", [9000, 9199])   # templates are clone-only (decision 32 D5)

reasons = {"A": [], "B": [], "C": []}
summary = {"create": 0, "update": 0, "delete": 0, "replace": 0, "import": 0}


def leaves(d, prefix=()):
    if isinstance(d, dict) and d:
        out = {}
        for k, v in d.items():
            out.update(leaves(v, prefix + (str(k),)))
        return out
    return {prefix: d}


def template_change(f):
    """Class of a change under terraform/templates/ (Faris decision 3, standards PR #17).
    B only for a minor refresh: a spec that exists in base and head whose only changed leaves are REFRESH_LEAVES,
    with the same url scheme+host (or no change at all after parsing). Everything else is C, failing closed."""
    if not (a.base_dir and a.head_dir):
        return "C", f"template file (no base/head to compare): {f}"
    if not TEMPLATE_SPEC.match(f):
        return "C", f"template support file (vendor key, nixos config): {f}"
    b, h = pathlib.Path(a.base_dir, f), pathlib.Path(a.head_dir, f)
    if not b.exists():
        return "C", f"new template spec: {f}"
    if not h.exists():
        return "C", f"retire template (spec deleted): {f}"
    try:
        old, new = yaml.safe_load(b.read_text()) or {}, yaml.safe_load(h.read_text()) or {}
    except yaml.YAMLError:
        return "C", f"unparsable template spec: {f}"
    if not isinstance(old, dict) or not isinstance(new, dict):
        return "C", f"template spec is not a mapping: {f}"
    lo, ln = leaves(old), leaves(new)
    diff = {k for k in set(lo) | set(ln) if lo.get(k, MISSING) != ln.get(k, MISSING)}
    if not diff:
        return "B", f"template spec reformatted (rebuilds the same image): {f}"
    extra = diff - REFRESH_LEAVES
    if extra:
        return "C", f"template change ({', '.join('.'.join(k) for k in sorted(extra))}): {f}"
    hosts = [urllib.parse.urlsplit(str((s.get("source") or {}).get("url") or "")) for s in (old, new)]
    if (hosts[0].scheme, hosts[0].netloc) != (hosts[1].scheme, hosts[1].netloc):
        return "C", f"template image host changed: {f}"
    return "B", f"minor template refresh ({', '.join('.'.join(k) for k in sorted(diff))}): {f}"


for f in changed["files"]:
    if f.startswith("terraform/templates/"):
        cls, why = template_change(f)
        reasons[cls].append(why)
    elif any(fnmatch.fnmatch(f, p) for p in C_PATHS):
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
        if rc.get("type") not in GUEST_TYPES:
            reasons["C"].append(f"host-level resource {rc.get('type')}: {addr}")
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
            if vmid is not None and tlo <= int(vmid) <= thi:
                reasons["C"].append(f"template VMID {vmid}: {addr}")
            for nic in side.get("network_interface") or []:
                if nic.get("bridge") in pbridges:
                    reasons["C"].append(f"protected bridge {nic.get('bridge')}: {addr}")

cls = "C" if reasons["C"] else "B" if reasons["B"] else "A"
print(json.dumps({"class": cls, "reasons": reasons[cls] or ["routine change"], "all_reasons": reasons, "summary": summary}))
