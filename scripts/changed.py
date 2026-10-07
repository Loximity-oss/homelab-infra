#!/usr/bin/env python3
"""What a diff touches. Usage: changed.py BASE HEAD  (run inside the head checkout)
Prints JSON: files, stacks (dirs to plan/apply), hosts (to configure), ansible_all."""
import json, subprocess, sys, pathlib, re
base, head = sys.argv[1], sys.argv[2]
files = subprocess.run(["git", "diff", "--name-only", f"{base}...{head}"], capture_output=True, text=True, check=True).stdout.split()
all_stacks = sorted(str(p.parent) for p in pathlib.Path(".").glob("terraform/stacks/*/*/main.tf"))
stacks, hosts = set(), set()
everything = any(f.startswith(("terraform/modules/", "ansible/keys/")) for f in files)
for f in files:
    m = re.match(r"(terraform/stacks/[^/]+/[^/]+)/", f)
    if m:
        stacks.add(m.group(1))
    m = re.match(r"terraform/stacks/[^/]+/[^/]+/guests/([a-z0-9-]+)\.yaml$", f)
    if m:
        hosts.add(m.group(1))
if everything:
    stacks = set(all_stacks)
stacks &= set(all_stacks)  # deleted stacks are ignored
ansible_all = any(f.startswith("ansible/") and not f.startswith("ansible/keys/") for f in files)
print(json.dumps({"files": files, "stacks": sorted(stacks), "hosts": sorted(hosts), "ansible_all": ansible_all}))
