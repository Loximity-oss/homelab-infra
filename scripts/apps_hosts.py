#!/usr/bin/env python3
"""Hosts listed in homelab-apps deployments (proposal 1.6 / 5.1, migration step 4b).
Usage: apps_hosts.py <apps checkout>  ->  {"base_ci_apps_hosts": [...]} on stdout (Ansible extra-vars file).
Reads deployments/<zone>/<app>.yaml `hosts:` only. Any unreadable or malformed file, or a bad hostname, exits 1:
ci-apply.sh then leaves existing ci-apps keys unchanged (never removes them on a partial view)."""
import json, pathlib, re, sys
import yaml

HOST = re.compile(r"^hm1-[a-z]+-[a-z][a-z0-9]*[0-9]{2}$")


def main(root):
    root = pathlib.Path(root)
    if not (root / ".git").exists() and not (root / "README.md").exists():
        sys.exit(f"{root}: not a homelab-apps checkout")
    hosts = set()
    for p in sorted(root.glob("deployments/*/*.yaml")):
        if p.is_symlink():
            sys.exit(f"{p}: symlink refused")
        d = yaml.safe_load(p.read_text())
        if not isinstance(d, dict):
            sys.exit(f"{p}: not a mapping")
        hs = d.get("hosts") or []
        if not isinstance(hs, list):
            sys.exit(f"{p}: hosts is not a list")
        for h in hs:
            if not isinstance(h, str) or not HOST.match(h):
                sys.exit(f"{p}: bad hostname {h!r}")
            hosts.add(h)
    print(json.dumps({"base_ci_apps_hosts": sorted(hosts)}))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
