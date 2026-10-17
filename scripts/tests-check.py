#!/usr/bin/env python3
"""Static check of the guest test files (decision 32), run in the PR gate against the PR tree. Never executes them:
renders tests/os/*.goss.yaml.j2 for every guest spec, and tests/apps/<role>.goss.yaml.j2 (L4) for every guest of that
role, with Jinja (sandboxed) and checks the Goss structure (e.g. a command needs exec + exit-status: issue #27).
Usage: tests-check.py <pr_tree>"""
import os, sys
import jinja2.sandbox, yaml

root = sys.argv[1]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # main's scripts: no code is imported from the PR tree
from specs import load_specs  # noqa: E402

gv = yaml.safe_load(open(os.path.join(root, "ansible/playbooks/group_vars/all.yml")))
env = jinja2.sandbox.SandboxedEnvironment(undefined=jinja2.StrictUndefined)
KEYS = {"command": {"exec", "exit-status", "stdout", "stderr", "timeout", "skip"},
        "package": {"installed", "versions", "skip"}, "file": {"exists", "filetype", "mode", "owner", "group", "contents", "skip"},
        "service": {"running", "enabled", "skip"}}
errs = []


def check(label, path, ctx):
    try:
        doc = yaml.safe_load(env.from_string(open(path).read()).render(**ctx)) or {}
    except Exception as e:
        errs.append(f"{label}: render/YAML error: {e}"); return
    for kind, items in doc.items():
        if kind not in KEYS:
            errs.append(f"{label}: unknown resource type '{kind}'"); continue
        for name, attrs in (items or {}).items():
            bad = set(attrs or {}) - KEYS[kind]
            if bad:
                errs.append(f"{label}: {kind} {name}: unknown keys {sorted(bad)}")
            if kind == "command" and not {"exec", "exit-status"} <= set(attrs or {}):
                errs.append(f"{label}: command {name}: needs exec and exit-status")


apps = 0
for s in load_specs(root):
    fam = "ubuntu" if s["os"].startswith("ubuntu") else s["os"]
    path = os.path.join(root, "tests/os", f"{fam}.goss.yaml.j2")
    if not os.path.exists(path):
        errs.append(f"{s['hostname']}: no tests/os/{fam}.goss.yaml.j2"); continue
    ctx = dict(gv, inventory_hostname=s["hostname"], verify_type=s.get("type", "lxc"), verify_os=s["os"],
               keyctl=s.get("keyctl", False), verify_max_used_pct=85)
    check(s["hostname"], path, ctx)
    app = os.path.join(root, "tests/apps", f"{s['role']}.goss.yaml.j2")
    if os.path.exists(app):   # L4: same vars the verify play loads for this host (all.yml + role_<role>.yml)
        rv = os.path.join(root, "ansible/playbooks/group_vars", f"role_{s['role']}.yml")
        actx = dict(ctx, **((yaml.safe_load(open(rv)) or {}) if os.path.exists(rv) else {}))
        check(f"{s['hostname']} (tests/apps/{s['role']})", app, actx)
        apps += 1
print("\n".join(errs) if errs else f"tests: ok ({len(load_specs(root))} guest(s) rendered, {apps} with app checks)")
sys.exit(1 if errs else 0)
