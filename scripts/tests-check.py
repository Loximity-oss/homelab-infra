#!/usr/bin/env python3
"""Static check of the guest test files (decision 32), run in the PR gate against the PR tree. Never executes them:
renders tests/os/*.goss.yaml.j2 for every guest spec with Jinja (sandboxed) and checks the Goss structure
(e.g. a command needs exec + exit-status: issue #27). Usage: tests-check.py <pr_tree>"""
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
for s in load_specs(root):
    fam = "ubuntu" if s["os"].startswith("ubuntu") else s["os"]
    path = os.path.join(root, "tests/os", f"{fam}.goss.yaml.j2")
    if not os.path.exists(path):
        errs.append(f"{s['hostname']}: no tests/os/{fam}.goss.yaml.j2"); continue
    try:
        txt = env.from_string(open(path).read()).render(
            inventory_hostname=s["hostname"], verify_type=s.get("type", "lxc"), verify_os=s["os"],
            keyctl=s.get("keyctl", False), verify_max_used_pct=85, **gv)
        doc = yaml.safe_load(txt) or {}
    except Exception as e:
        errs.append(f"{s['hostname']}: render/YAML error: {e}"); continue
    for kind, items in doc.items():
        if kind not in KEYS:
            errs.append(f"{s['hostname']}: unknown resource type '{kind}'"); continue
        for name, attrs in (items or {}).items():
            bad = set(attrs or {}) - KEYS[kind]
            if bad:
                errs.append(f"{s['hostname']}: {kind} {name}: unknown keys {sorted(bad)}")
            if kind == "command" and not {"exec", "exit-status"} <= set(attrs or {}):
                errs.append(f"{s['hostname']}: command {name}: needs exec and exit-status")
print("\n".join(errs) if errs else f"tests: ok ({len(load_specs(root))} guest(s) rendered)")
sys.exit(1 if errs else 0)
