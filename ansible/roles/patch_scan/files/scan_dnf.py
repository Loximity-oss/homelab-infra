#!/usr/bin/env python3
"""Read-only scan on a Fedora guest: pending upgrades with their advisory severity (Critical/Important/Moderate/Low).
Prints one JSON document. Changes nothing."""
import json, subprocess


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout


adv = {}  # package name -> list of {id, severity, type}
for line in run(["dnf", "-q", "updateinfo", "list", "--updates"]).splitlines():
    t = line.split()
    if len(t) < 3:
        continue
    kind = t[1]
    sev = kind.split("/")[0] if "/Sec" in kind else None
    name = t[-1].rsplit("-", 2)[0]
    adv.setdefault(name, []).append({"id": t[0], "severity": sev, "type": kind})

pkgs = []
for line in run(["dnf", "-q", "repoquery", "--upgrades", "--qf", "%{name} %{evr} %{arch}\\n"]).splitlines():
    t = line.split()
    if len(t) != 3:
        continue
    name, evr, arch = t
    inst = run(["rpm", "-q", "--qf", "%{EVR}", name]).strip()
    a = adv.get(name, [])
    pkgs.append({"name": name, "installed": inst or None, "candidate": evr, "arch": arch,
                 "security": any(x["severity"] for x in a), "advisories": a})
# needs-restarting -r: rc 1 = reboot recommended
rc = subprocess.run(["dnf", "needs-restarting", "-r"], capture_output=True).returncode
print(json.dumps({"family": "redhat", "reboot_required": rc == 1, "packages": pkgs}))
