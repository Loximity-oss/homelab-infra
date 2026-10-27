#!/usr/bin/env python3
"""Runner side: raw scan -> Sev1-4 per package (patching policy).
  Sev1 critical, Sev2 high (security)  -> auto-applied on ring 1
  Sev3 medium / unclassified security, Sev4 low / non-security -> monthly bundle (Faris approves)
Ubuntu: highest Ubuntu CVE priority over the new changelog entries (critical=1, high=2, medium=3, low/negligible=4).
Fedora: advisory severity (Critical=1, Important=2, Moderate=3, Low=4).
Fail safe: a security update whose severity cannot be resolved is Sev3 (never auto-applied).
Ubuntu lookups: shared on-disk cache (7-day TTL), 4 in parallel, retries on 429/5xx, and a total time budget;
whatever is not resolved within the budget falls to the Sev3 fail safe (infra #169: ~190 CVEs per host took > 1 h).
usage: classify.py raw.json out.json host reboot_regex ubuntu_cve_url [cache.json budget_s]"""
import fcntl, json, os, re, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

raw_p, out_p, host, reboot_re, url = sys.argv[1:6]
cache_p = sys.argv[6] if len(sys.argv) > 6 else ""
budget = float(sys.argv[7]) if len(sys.argv) > 7 else 600
raw = json.load(open(raw_p))
UBU = {"critical": 1, "high": 2, "medium": 3, "low": 4, "negligible": 4}
RPM = {"Critical": 1, "Important": 2, "Moderate": 3, "Low": 4}
TTL, WORKERS, TIMEOUT, TRIES = 7 * 86400, 4, 10, 3
deadline = time.monotonic() + budget


def fetch(cve):
    for n in range(TRIES):
        left = deadline - time.monotonic()
        if left <= 1:
            return None
        try:
            d = json.load(urllib.request.urlopen(url.format(cve=cve), timeout=min(TIMEOUT, left)))
            return str(d.get("priority", "")).lower() or None
        except urllib.error.HTTPError as e:
            if e.code == 404 or (e.code < 500 and e.code != 429):
                return None
        except Exception:
            pass
        time.sleep(min(2 ** n, max(0, deadline - time.monotonic())))
    return None


def cache_io(fresh=None):
    """Read the shared cache, or merge fresh entries into it; flock only around the file, never around lookups."""
    if not cache_p:
        return {}
    with open(cache_p + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            cache = json.load(open(cache_p))
        except (OSError, ValueError):
            cache = {}
        if fresh:
            now = time.time()
            cache = {c: v for c, v in cache.items() if now - v["t"] < TTL} | fresh
            tmp = f"{cache_p}.{os.getpid()}"
            json.dump(cache, open(tmp, "w"))
            os.replace(tmp, cache_p)
        return cache


def ubuntu_priorities(cves):
    """{cve: priority or None}; resolved priorities are shared across hosts and runs via cache_p."""
    if not cves:
        return {}
    cache, now = cache_io(), time.time()
    res = {c: cache[c]["p"] for c in cves if c in cache and now - cache[c]["t"] < TTL}
    todo = sorted(set(cves) - set(res))
    with ThreadPoolExecutor(WORKERS) as ex:
        got = dict(zip(todo, ex.map(fetch, todo)))
    res.update(got)
    fresh = {c: {"p": p, "t": now} for c, p in got.items() if p in UBU}
    cache_io(fresh)
    print(f"{host}: {len(cves)} CVEs, {len(cves) - len(todo)} cached, {len(fresh)} fetched, "
          f"{len(todo) - len(fresh)} unresolved", file=sys.stderr)
    return res


if raw["family"] == "debian":
    prio = ubuntu_priorities(sorted({c for p in raw["packages"] if p["security"] for c in p["cves"]}))

for p in raw["packages"]:
    if not p["security"]:
        p["severity"], p["basis"] = 4, "not a security update"
    elif raw["family"] == "debian":
        known = [UBU[prio[c]] for c in p["cves"] if prio.get(c) in UBU]
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
