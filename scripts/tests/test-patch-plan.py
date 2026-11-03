#!/usr/bin/env python3
"""Unit tests for the report format of scripts/patch_plan.py (infra #206): CVE count, top links, body size.
No network, no Gitea. Run: python3 scripts/tests/test-patch-plan.py"""
import importlib.machinery, importlib.util, json, os, pathlib, tempfile, unittest

SRC = pathlib.Path(__file__).resolve().parents[1] / "patch_plan.py"
loader = importlib.machinery.SourceFileLoader("patch_plan", str(SRC))
spec = importlib.util.spec_from_loader("patch_plan", loader)
pp = importlib.util.module_from_spec(spec)
loader.exec_module(pp)

CVES = [f"CVE-2026-{53100 + i}" for i in range(60)] + ["CVE-2023-20585"]


def pkg(name="bpftool", cves=CVES, **kw):
    return {"name": name, "installed": "7.7.0+7.0.0-38.38", "candidate": "7.7.0+7.0.0-42.42", "severity": 1,
            "basis": "ubuntu cve priority", "cves": list(cves), "advisories": [], "reboot_hint": False, **kw}


class Refs(unittest.TestCase):
    def setUp(self):
        pp.PRIO.clear()

    def test_top_three_by_priority_then_more(self):
        pp.PRIO.update({"CVE-2023-20585": "critical", "CVE-2026-53110": "high", "CVE-2026-53101": "high"})
        r = pp.refs(pkg())
        self.assertEqual(r.count("](https://ubuntu.com/security/"), 3)
        self.assertTrue(r.startswith("[CVE-2023-20585]"))
        self.assertLess(r.index("CVE-2026-53110"), r.index("CVE-2026-53101"))   # newest first within a priority
        self.assertIn("+58 more", r)

    def test_no_cache_newest_first(self):
        r = pp.refs(pkg())
        self.assertTrue(r.startswith("[CVE-2026-53159]"))

    def test_source_link_and_none(self):
        self.assertIn("cves?package=linux", pp.refs(pkg(source="linux")))
        self.assertEqual(pp.refs(pkg(cves=[])), "-")
        self.assertEqual(pp.refs(pkg(cves=["CVE-2026-1"]), top=0), "1 not listed")

    def test_advisories_ranked(self):
        p = pkg(cves=[], advisories=[{"id": "FEDORA-1", "severity": "Low"}, {"id": "FEDORA-2", "severity": "Critical"}])
        self.assertTrue(pp.refs(p).startswith("[FEDORA-2]"))

    def test_row_has_count_and_seven_columns(self):
        row = pp.table([pkg()]).splitlines()[2]
        self.assertEqual(row.count(" | "), 6)
        self.assertIn("| 61 |", row)
        self.assertIn("7.7.0+7.0.0-38.38 -> 7.7.0+7.0.0-42.42", row)


class Summary(unittest.TestCase):
    def run_summary(self, n_pkgs, cache=None):
        pp.PRIO.clear()
        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, "20261103_0340-scan"); os.mkdir(d)
            if cache:
                json.dump(cache, open(os.path.join(root, "ubuntu-cve-cache.json"), "w"))
            pk = [pkg(f"linux-pkg-{i:03d}") for i in range(n_pkgs)]
            json.dump({"host": "hm1-shr-k3sc01", "packages": pk}, open(os.path.join(d, "hm1-shr-k3sc01.scan.json"), "w"))
            json.dump({"host": "hm1-shr-k3sc01", "applied": [p["name"] for p in pk], "snapshot": "pre-patch_20261103_0340",
                       "max_sev": 2, "reboot_pending": "true"}, open(os.path.join(d, "hm1-shr-k3sc01.apply.json"), "w"))
            out = os.path.join(root, "summary.md")
            pp.summary(d, "Sev1/2 auto-apply 20261103_0340 (ok)", out)
            return open(out).read()

    def test_header_kept_and_cache_read(self):
        md = self.run_summary(2, {"CVE-2023-20585": {"p": "critical", "t": 0}})
        self.assertIn("### `hm1-shr-k3sc01` (snapshot `pre-patch_20261103_0340`, max Sev2)", md)
        self.assertIn("Reboot pending: yes", md)
        self.assertIn("[CVE-2023-20585](https://ubuntu.com/security/CVE-2023-20585) critical", md)
        self.assertIn("+58 more", md)

    def test_many_packages_stay_under_limit(self):
        md = self.run_summary(400)
        self.assertLessEqual(len(md.encode()), pp.BODY_MAX)
        self.assertIn("max Sev2", md)


if __name__ == "__main__":
    unittest.main()
