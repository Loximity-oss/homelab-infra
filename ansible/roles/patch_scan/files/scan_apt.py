#!/usr/bin/env python3
"""Read-only scan on a Debian/Ubuntu guest: pending upgrades from `apt-get -s dist-upgrade`.
For packages from a -security pocket the CVE ids of the new changelog entries are collected (max 5 entries).
Prints one JSON document. Changes nothing."""
import json, os, re, subprocess

INST = re.compile(r'^Inst (\S+)(?: \[(\S+)\])? \((\S+) (.*?)(?: \[\S+\])?\)(?: \[.*\])?\s*$')
HDR = re.compile(r'^\S+ \((\S+)\) ')
CVE = re.compile(r'CVE-\d{4}-\d{4,}')


def cves(pkg, installed):
    try:
        out = subprocess.run(["apt-get", "changelog", pkg], capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return []
    found, headers = set(), 0
    for line in out.splitlines():
        m = HDR.match(line)
        if m:
            if installed and m.group(1) == installed:
                break
            headers += 1
            if headers > 5:
                break
        found.update(CVE.findall(line))
    return sorted(found)


sim = subprocess.run(["apt-get", "-s", "-o", "Debug::NoLocking=1", "dist-upgrade"], capture_output=True, text=True).stdout
pkgs = []
for line in sim.splitlines():
    m = INST.match(line)
    if not m:
        continue
    name, installed, candidate, origins = m.groups()
    security = "-security" in origins
    pkgs.append({"name": name, "installed": installed, "candidate": candidate, "origins": origins,
                 "security": security, "cves": cves(name, installed) if security else []})
print(json.dumps({"family": "debian", "reboot_required": os.path.exists("/var/run/reboot-required"), "packages": pkgs}))
