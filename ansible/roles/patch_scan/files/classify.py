#!/usr/bin/env python3
"""Runner side: raw scan -> Sev1-4 per package (patching policy).
  Sev1 critical, Sev2 high (security)  -> auto-applied on ring 1
  Sev3 medium / unclassified security, Sev4 low / non-security -> monthly bundle (Faris approves)
Ubuntu: highest Ubuntu CVE priority over the new changelog entries (critical=1, high=2, medium=3, low/negligible=4).
Fedora: advisory severity (Critical=1, Important=2, Moderate=3, Low=4).
Fail safe: a security update whose severity cannot be resolved is Sev3 (never auto-applied).
usage: classify.py raw.json out.json host reboot_regex ubuntu_cve_url"""
import json, re, sys, time, urllib.request

raw_p, out_p, host, reboot_re, url = sys.argv[1:6]
raw = json.load(open(raw_p))
UBU = {"critical": 1, "high": 2, "medium": 3, "low": 4, "negligible": 4}
RPM = {"Critical": 1, "Important": 2, "Moderate": 3, "Low": 4}
cache = {}


def cve_sev(cve):
    if cve not in cache:
        sev = None
        for _ in range(2):
            try:
                d = json.load(urllib.request.urlopen(url.format(cve=cve), timeout=20))
                sev = UBU.get(str(d.get("priority", "")).lower())
                break
            except Exception:
                time.sleep(2)
        cache[cve] = sev
    return cache[cve]


for p in raw["packages"]:
    if not p["security"]:
        p["severity"], p["basis"] = 4, "not a security update"
    elif raw["family"] == "debian":
        sevs = [cve_sev(c) for c in p["cves"]]
        known = [s for s in sevs if s]
        if known:
            p["severity"], p["basis"] = min(known), "ubuntu cve priority"
        else:
            p["severity"], p["basis"] = 3, "security update, severity unresolved (fail safe)"
    else:
        known = [RPM[a["severity"]] for a in p["advisories"] if a.get("severity") in RPM]
        p["severity"], p["basis"] = (min(known), "advisory severity") if known else (3, "security update, severity unresolved (fail safe)")
    p["reboot_hint"] = bool(re.search(reboot_re, p["name"]))

counts = {f"sev{n}": sum(1 for p in raw["packages"] if p["severity"] == n) for n in (1, 2, 3, 4)}
raw.update({"host": host, "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "counts": counts,
            "reboot_hint": raw["reboot_required"] or any(p["reboot_hint"] for p in raw["packages"])})
json.dump(raw, open(out_p, "w"), indent=1)
