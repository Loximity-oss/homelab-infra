#!/usr/bin/env python3
"""Pipeline side of ring-1 patching (docs/patching-pipeline.md). Reads what the patch_scan / patch_apply roles write
to $PATCH_OUT (<host>.scan.json, <host>.apply.json). Never reboots; never selects ring0, protected or template VMIDs.
  patch_plan.py sev12   DIR STANDARDS               ring-1 hosts with counts.sev1 + counts.sev2 > 0 (comma list)
  patch_plan.py reboot  DIR                         open/comment 'patch: reboot <host>'; close when no longer pending
  patch_plan.py bundle  DIR STANDARDS MONTH OUTDIR  OUTDIR/<MONTH>.json + .md (Sev3/4 per ring-1 host); prints host count
  patch_plan.py allow   BUNDLE STANDARDS OUT        extra vars {patch_bundle_allow: {host: [pkg]}}; prints hosts
  patch_plan.py summary DIR TITLE OUT               markdown of the apply results"""
import calendar, datetime, glob, hashlib, json, os, subprocess, sys, tempfile
import yaml

ROOT = os.environ.get("INFRA_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from specs import load_specs  # noqa: E402

HARD_PROTECTED = {300, 301, 303}   # protected.yaml is read too; these are refused even if it were missing
KEYS = ("name", "installed", "candidate", "severity", "basis", "cves", "advisories", "reboot_hint")
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


def refs(p):
    r = [f"[{c}](https://ubuntu.com/security/{c})" for c in p.get("cves") or []]
    r += [f"[{a['id']}](https://bodhi.fedoraproject.org/updates/{a['id']})" for a in p.get("advisories") or [] if a.get("id")]
    return " ".join(r) or "-"


def table(pkgs):
    rows = ["| Package | Installed | New | Sev | Basis | CVE / advisory | Reboot hint |", "|---|---|---|---|---|---|---|"]
    for p in sorted(pkgs, key=lambda p: (p.get("severity") or 9, p["name"])):
        rows.append(f"| `{p['name']}` | {p.get('installed') or '-'} | {p.get('candidate') or '-'} | Sev{p.get('severity')} | "
                    f"{p.get('basis', '')} | {refs(p)} | {'yes' if p.get('reboot_hint') else ''} |")
    return "\n".join(rows)


def gitea(*args):
    subprocess.run(["python3", os.path.join(ROOT, "scripts", "gitea.py"), *args], check=False)


def sev12(d, std):
    r1, sc = ring1(std), results(d, "scan")
    def n(h):
        c = sc[h].get("counts") or {}
        return int(c.get("sev1", 0)) + int(c.get("sev2", 0))
    print(",".join(sorted(h for h in sc if h in r1 and n(h) > 0)))


def reboot(d):
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
        body = [f"**Reboot pending** for `{h}` (vmid {sp.get('vmid', '?')}, zone {sp.get('_zone', '?')}).", "", REBOOT_RULE, ""]
        if a and applied:
            body += [f"### Applied (snapshot `{a.get('snapshot')}`, max Sev{a.get('max_sev')})", "", table(done) if done else
                     ", ".join(f"`{x}`" for x in sorted(applied)), ""]
        if hint:
            body += ["### Still pending, usually need a reboot", "", table(hint), ""]
        body += ["Release notes: CVE / advisory links above; full changelog on the guest with `apt changelog <pkg>` "
                 "or `dnf updateinfo info <advisory>`.", "", f"<!-- digest:{dig} -->"]
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("\n".join(body) + "\n")
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
    md = [f"Monthly Sev3/4 patch bundle **{month}** for ring 1: {total} package(s) on {len(hosts)} host(s).", "",
          f"**Faris:** approve this PR (Class C) and merge it to have it applied on **{when} 02:00 MYT** "
          "(`patch.yml -e patch_max_sev=4`, one host at a time, `pre-patch_` snapshot first, verify after). "
          "Only the packages listed here are applied as Sev3/4; Sev1/2 are applied automatically anyway. "
          "Not approved by then = not applied; next month's bundle supersedes it. Nothing is ever rebooted: "
          "a pending reboot opens a `patch: reboot <host>` issue for your answer.", "",
          f"Reboot hint after this bundle: {', '.join(f'`{h}`' for h in reb) or 'none'}.", ""]
    if missing:
        md += [f"Not scanned (unreachable or failed scan, not in this bundle): {', '.join(f'`{h}`' for h in missing)}.", ""]
    for h, pk in hosts.items():
        md += [f"### `{h}`", "", table(pk), ""]
    md += ["Release notes: CVE / advisory links per package; full changelog on the guest with `apt changelog <pkg>` or "
           "`dnf updateinfo info <advisory>`. Machine-readable list: `patches/hm1/" + month + ".json`.", "",
           "_Opened by the `patch-bundle` workflow (docs/patching-pipeline.md)._"]
    with open(os.path.join(outdir, f"{month}.md"), "w") as f:
        f.write("\n".join(md) + "\n")
    print(len(hosts))


def allow(path, std, out):
    b, r1 = json.load(open(path)), ring1(std)
    al = {h: sorted({p["name"] for p in pk}) for h, pk in (b.get("hosts") or {}).items() if h in r1 and pk}
    with open(out, "w") as f:
        json.dump({"patch_bundle_allow": al}, f, indent=1, sort_keys=True)
    print(",".join(sorted(al)))


def summary(d, title, out):
    sc, ap = results(d, "scan"), results(d, "apply")
    md = [f"## {title}", ""]
    if not ap:
        md += ["No host finished patching (see the run log / `patch: failed` issue).", ""]
    for h in sorted(ap):
        a = ap[h]
        applied = set(as_list(a.get("applied")))
        done = [p for p in (sc.get(h) or {}).get("packages", []) if p["name"] in applied]
        md += [f"### `{h}` (snapshot `{a.get('snapshot')}`, max Sev{a.get('max_sev')})", "",
               table(done) if done else ("Applied: " + (", ".join(f"`{x}`" for x in sorted(applied)) or "nothing")), "",
               "Deferred: " + (", ".join(f"`{x}`" for x in as_list(a.get("deferred"))) or "-"),
               "Reboot pending: " + (f"yes, issue `patch: reboot {h}`" if truthy(a.get("reboot_pending")) else "no"), ""]
    with open(out, "w") as f:
        f.write("\n".join(md) + "\n")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    {"sev12": sev12, "reboot": reboot, "bundle": bundle, "allow": allow, "summary": summary}[cmd](*args)
