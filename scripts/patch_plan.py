#!/usr/bin/env python3
"""Pipeline side of ring-1 patching (docs/patching-pipeline.md). Reads what the patch_scan / patch_apply roles write
to $PATCH_OUT (<host>.scan.json, <host>.apply.json). Never reboots; never selects ring0, protected or template VMIDs.
  patch_plan.py sev12   DIR STANDARDS               ring-1 hosts with counts.sev1 + counts.sev2 > 0 (comma list)
  patch_plan.py reboot  DIR                         open/comment 'patch: reboot <host>'; close when no longer pending
  patch_plan.py bundle  DIR STANDARDS MONTH OUTDIR  OUTDIR/<MONTH>.json + .md (Sev3/4 per ring-1 host); prints host count
  patch_plan.py allow   BUNDLE STANDARDS OUT        extra vars {patch_bundle_allow: {host: [pkg]}}; prints hosts
  patch_plan.py summary DIR TITLE OUT               markdown of the apply results"""
import calendar, datetime, glob, hashlib, json, os, re, subprocess, sys, tempfile, urllib.parse
import yaml

ROOT = os.environ.get("INFRA_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from specs import load_specs  # noqa: E402

HARD_PROTECTED = {300, 301, 303}   # protected.yaml is read too; these are refused even if it were missing
KEYS = ("name", "installed", "candidate", "severity", "basis", "cves", "advisories", "reboot_hint", "source")
# Report format only (infra #206): per package a CVE count and the TOP_REFS highest-priority links, then "+N more".
# The full lists stay in <host>.scan.json on the runner and in the bundle .json. Never changes Sev or selection.
TOP_REFS = 3
BODY_MAX = 30000   # bytes per issue / comment / PR body; Gitea's limit is 65535, stay well below it
PRIO_RANK = {"critical": 1, "high": 2, "medium": 3, "low": 4, "negligible": 5}
RPM_RANK = {"Critical": 1, "Important": 2, "Moderate": 3, "Low": 4}
PRIO = {}          # CVE -> Ubuntu priority from classify.py's runner cache; only orders the links
REBOOT_RULE = ("The pipeline never reboots. **Faris:** answer in this issue (approve or defer). There is no timeout. "
               "When approved: one host at a time; k3s control nodes drained first, wait for `Ready` and healthy etcd "
               "before the next, never two control nodes at once, workers one at a time (docs/apps/k3s.md).")


def truthy(v):
    return str(v).strip().lower() in ("true", "1", "yes")


def ring1(std):
    prot = yaml.safe_load(open(os.path.join(std, "protected.yaml"))) or {}
    never = {int(v) for v in prot.get("vmids", {})} | HARD_PROTECTED
    lo, hi = prot.get("templates", [9000, 9199])
    return {s["hostname"]: s for s in load_specs()
            if s.get("patch") == "ring1" and int(s["vmid"]) not in never and not lo <= int(s["vmid"]) <= hi}


def results(d, kind):
    out = {}
    for p in sorted(glob.glob(os.path.join(d, f"*.{kind}.json"))):
        try:
            j = json.load(open(p))
        except (OSError, ValueError):
            continue
        out[j.get("host") or os.path.basename(p)[: -len(f".{kind}.json")]] = j
    return out


def as_list(v):
    if isinstance(v, str):
        try:
            v = json.loads(v.replace("'", '"'))
        except ValueError:
            return [v] if v else []
    return list(v or [])


def load_prio(d):
    """Ubuntu CVE priorities from the patch_scan cache (<PATCH_OUT>/../ubuntu-cve-cache.json, written by classify.py).
    Display only: decides which CVE links are shown first. No cache -> newest CVE ids first."""
    p = os.environ.get("PATCH_CVE_CACHE") or os.path.join(os.path.dirname(os.path.abspath(d)), "ubuntu-cve-cache.json")
    try:
        c = json.load(open(p))
        PRIO.update({k: str(v.get("p") or "").lower() for k, v in c.items() if isinstance(v, dict)})
    except (OSError, ValueError, AttributeError):
        pass


def cve_key(c):
    m = re.fullmatch(r"CVE-(\d+)-(\d+)", c)
    y, n = (int(m[1]), int(m[2])) if m else (0, 0)
    return (PRIO_RANK.get(PRIO.get(c), 9), -y, -n, c)


def refs(p, top=TOP_REFS):
    """The `top` highest-priority CVE / advisory links, then '+N more'; a link to all CVEs of the source package
    when the scan provides `source`."""
    links = [(cve_key(c)[0], f"[{c}](https://ubuntu.com/security/{c})" + (f" {PRIO[c]}" if PRIO.get(c) in PRIO_RANK else ""))
             for c in sorted(set(p.get("cves") or []), key=cve_key)]
    advs = {a["id"]: a for a in p.get("advisories") or [] if a.get("id")}.values()
    links += [(RPM_RANK.get(a.get("severity"), 9), f"[{a['id']}](https://bodhi.fedoraproject.org/updates/{a['id']})"
               + (f" {a['severity']}" if a.get("severity") else "")) for a in sorted(advs, key=lambda a: a["id"])]
    if not links:
        return "-"
    links.sort(key=lambda x: x[0])   # stable: newest CVE first within a priority
    shown = [l for _, l in links[:top]]
    more = len(links) - len(shown)
    if more:
        shown.append(f"+{more} more" if shown else f"{more} not listed")
    if p.get("source") and p.get("cves"):
        shown.append(f"([all](https://ubuntu.com/security/cves?package={urllib.parse.quote(str(p['source']))}))")
    return " ".join(shown)


def table(pkgs, top=TOP_REFS):
    rows = ["| Package | Installed -> New | Sev | Basis | CVEs | Top CVE / advisory | Reboot hint |",
            "|---|---|---|---|---|---|---|"]
    for p in sorted(pkgs, key=lambda p: (p.get("severity") or 9, p["name"])):
        n = len(set(p.get("cves") or []))
        rows.append(f"| `{p['name']}` | {p.get('installed') or '-'} -> {p.get('candidate') or '-'} | Sev{p.get('severity')} | "
                    f"{p.get('basis', '')} | {n or '-'} | {refs(p, top)} | {'yes' if p.get('reboot_hint') else ''} |")
    return "\n".join(rows)


def fit(build):
    """build(top) -> markdown. Fewer links per package until it fits BODY_MAX bytes; last resort: cut at a line."""
    for top in (TOP_REFS, 1, 0):
        text = build(top)
        if len(text.encode()) <= BODY_MAX:
            return text
    cut = text.encode()[:BODY_MAX - 400].decode(errors="ignore").rsplit("\n", 1)[0]
    return (cut + f"\n\n_Cut at {BODY_MAX} bytes: the full per-package results are on the runner "
            f"(`{os.environ.get('PATCH_OUT') or '$PATCH_OUT'}`)._\n")


def gitea(*args):
    subprocess.run(["python3", os.path.join(ROOT, "scripts", "gitea.py"), *args], check=False)


def sev12(d, std):
    r1, sc = ring1(std), results(d, "scan")
    def n(h):
        c = sc[h].get("counts") or {}
        return int(c.get("sev1", 0)) + int(c.get("sev2", 0))
    print(",".join(sorted(h for h in sc if h in r1 and n(h) > 0)))


def reboot(d):
    load_prio(d)
    sc, ap = results(d, "scan"), results(d, "apply")
    specs = {s["hostname"]: s for s in load_specs()}
    for h in sorted(set(sc) | set(ap)):
        s, a = sc.get(h) or {}, ap.get(h)
        pending = (truthy(a.get("reboot_pending")) if a else False) or bool(s.get("reboot_required"))
        title = f"patch: reboot {h}"
        if not pending:
            if s:   # scanned now and nothing pending: the reboot happened (or was never needed)
                gitea("issue-close", title, f"`{h}` reports no pending reboot anymore (scan {s.get('scanned_at', '?')}): closing.")
            continue
        applied = set(as_list((a or {}).get("applied")))
        done = [p for p in s.get("packages", []) if p["name"] in applied]
        hint = [p for p in s.get("packages", []) if p.get("reboot_hint") and p["name"] not in applied]
        # one comment per patch run that leaves a reboot pending; a scan-only run opens the issue once
        key = json.dumps([h, sorted(applied), (a or {}).get("snapshot")] if a and applied else [h, "pending"])
        dig = hashlib.sha256(key.encode()).hexdigest()[:16]
        sp = specs.get(h, {})
        head = [f"**Reboot pending** for `{h}` (vmid {sp.get('vmid', '?')}, zone {sp.get('_zone', '?')}).", "", REBOOT_RULE, ""]

        def build(top):
            body = list(head)
            if a and applied:
                body += [f"### Applied (snapshot `{a.get('snapshot')}`, max Sev{a.get('max_sev')})", "", table(done, top) if done
                         else ", ".join(f"`{x}`" for x in sorted(applied)), ""]
            if hint:
                body += ["### Still pending, usually need a reboot", "", table(hint, top), ""]
            body += ["Release notes: highest-priority CVE / advisory links above (count per package); full changelog on "
                     "the guest with `apt changelog <pkg>` or `dnf updateinfo info <advisory>`."]
            return "\n".join(body) + "\n"
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(fit(build) + f"\n<!-- digest:{dig} -->\n")
        gitea("issue-ensure", title, "patch", f.name)
        os.unlink(f.name)


def apply_window(y, m):
    """First last-Saturday 02:00 MYT after the 25th (the bundle is opened on the 25th at 09:00)."""
    for _ in range(2):
        last = datetime.date(y, m, calendar.monthrange(y, m)[1])
        sat = last - datetime.timedelta(days=(last.weekday() - 5) % 7)
        if sat.day > 25:
            return sat
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return sat


def bundle(d, std, month, outdir):
    load_prio(d)
    r1, sc = ring1(std), results(d, "scan")
    hosts = {}
    for h in sorted(set(sc) & set(r1)):
        pk = [{k: p.get(k) for k in KEYS} for p in sc[h].get("packages", []) if p.get("severity") in (3, 4)]
        if pk:
            hosts[h] = pk
    y, m = map(int, month.split("-"))
    when = apply_window(y, m)
    missing = sorted(set(r1) - set(sc))
    doc = {"month": month, "apply": f"{when} 02:00 MYT", "scanned": sorted(sc), "not_scanned": missing, "hosts": hosts}
    with open(os.path.join(outdir, f"{month}.json"), "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    total = sum(len(v) for v in hosts.values())
    reb = sorted(h for h, pk in hosts.items() if any(p.get("reboot_hint") for p in pk))
    head = [f"Monthly Sev3/4 patch bundle **{month}** for ring 1: {total} package(s) on {len(hosts)} host(s).", "",
          f"**Faris:** approve this PR (Class C) and merge it to have it applied on **{when} 02:00 MYT** "
          "(`patch.yml -e patch_max_sev=4`, one host at a time, `pre-patch_` snapshot first, verify after). "
          "Only the packages listed here are applied as Sev3/4; Sev1/2 are applied automatically anyway. "
          "Not approved by then = not applied; next month's bundle supersedes it. Nothing is ever rebooted: "
          "a pending reboot opens a `patch: reboot <host>` issue for your answer.", "",
          f"Reboot hint after this bundle: {', '.join(f'`{h}`' for h in reb) or 'none'}.", ""]
    if missing:
        head += [f"Not scanned (unreachable or failed scan, not in this bundle): {', '.join(f'`{h}`' for h in missing)}.", ""]

    def build(top):
        md = list(head)
        for h, pk in hosts.items():
            md += [f"### `{h}`", "", table(pk, top), ""]
        md += ["Release notes: highest-priority CVE / advisory links per package (all of them in the .json); full "
               "changelog on the guest with `apt changelog <pkg>` or `dnf updateinfo info <advisory>`. Machine-readable "
               "list: `patches/hm1/" + month + ".json`.", "", "_Opened by the `patch-bundle` workflow (docs/patching-pipeline.md)._"]
        return "\n".join(md) + "\n"
    with open(os.path.join(outdir, f"{month}.md"), "w") as f:
        f.write(fit(build))
    print(len(hosts))


def allow(path, std, out):
    b, r1 = json.load(open(path)), ring1(std)
    al = {h: sorted({p["name"] for p in pk}) for h, pk in (b.get("hosts") or {}).items() if h in r1 and pk}
    with open(out, "w") as f:
        json.dump({"patch_bundle_allow": al}, f, indent=1, sort_keys=True)
    print(",".join(sorted(al)))


def summary(d, title, out):
    load_prio(d)
    sc, ap = results(d, "scan"), results(d, "apply")

    def build(top):
        md = [f"## {title}", ""]
        if not ap:
            md += ["No host finished patching (see the run log / `patch: failed` issue).", ""]
        for h in sorted(ap):
            a = ap[h]
            applied = set(as_list(a.get("applied")))
            done = [p for p in (sc.get(h) or {}).get("packages", []) if p["name"] in applied]
            md += [f"### `{h}` (snapshot `{a.get('snapshot')}`, max Sev{a.get('max_sev')})", "",
                   table(done, top) if done else ("Applied: " + (", ".join(f"`{x}`" for x in sorted(applied)) or "nothing")), "",
                   "Deferred: " + (", ".join(f"`{x}`" for x in as_list(a.get("deferred"))) or "-"),
                   "Reboot pending: " + (f"yes, issue `patch: reboot {h}`" if truthy(a.get("reboot_pending")) else "no"), ""]
        return "\n".join(md) + "\n"
    with open(out, "w") as f:
        f.write(fit(build))


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    {"sev12": sev12, "reboot": reboot, "bundle": bundle, "allow": allow, "summary": summary}[cmd](*args)
