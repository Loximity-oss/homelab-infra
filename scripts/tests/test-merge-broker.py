#!/usr/bin/env python3
"""Unit tests for host/merge-broker (infra #135): decision rules and class.json trust. No network, no OpenBao.
Run: python3 scripts/tests/test-merge-broker.py"""
import importlib.machinery, importlib.util, json, os, pathlib, sys, tempfile, unittest

SRC = pathlib.Path(__file__).resolve().parents[2] / "host/merge-broker/merge-broker"
loader = importlib.machinery.SourceFileLoader("merge_broker", str(SRC))
spec = importlib.util.spec_from_loader("merge_broker", loader)
mb = importlib.util.module_from_spec(spec)
loader.exec_module(mb)

SHA, OLD = "a" * 40, "b" * 40
REPO = "homelab/homelab-infra"


def pr(title="feat(x): y", author="oc-infra", labels=(), draft=False):
    return {"number": 7, "title": title, "draft": draft, "user": {"login": author},
            "labels": [{"name": l} for l in labels], "head": {"sha": SHA}}


def rv(user, state, commit=SHA, i=1, **kw):
    return {"id": i, "user": {"login": user}, "state": state, "commit_id": commit, **kw}


def status(context=mb.CHECK, state="success", creator=None, url=f"/{REPO}/actions/runs/516/jobs/519"):
    return {"statuses": [{"context": context, "status": state, "creator": creator, "target_url": url}]}


class Decide(unittest.TestCase):
    def d(self, cls, reviews=(), p=None, check=True):
        return mb.decide(p or pr(), cls, check, list(reviews))[0]

    def test_a_merges_without_review(self):
        self.assertTrue(self.d("A"))

    def test_no_class_never_merges(self):
        for c in (None, "", "D", "a"):
            self.assertFalse(self.d(c, [rv("faris", "APPROVED")]))

    def test_red_check_never_merges(self):
        self.assertFalse(self.d("A", check=False))

    def test_wip_and_draft(self):
        self.assertFalse(self.d("A", p=pr(title="WIP: x")))
        self.assertFalse(self.d("A", p=pr(labels=["wip"])))
        self.assertFalse(self.d("A", p=pr(draft=True)))

    def test_b_needs_rev_or_faris(self):
        self.assertFalse(self.d("B"))
        self.assertTrue(self.d("B", [rv("oc-rev", "APPROVED")]))
        self.assertTrue(self.d("B", [rv("faris", "APPROVED")]))
        self.assertFalse(self.d("B", [rv("oc-cfg", "APPROVED")]))
        self.assertFalse(self.d("B", [rv("ci-merger", "APPROVED")]))

    def test_c_needs_faris_only(self):
        self.assertFalse(self.d("C", [rv("oc-rev", "APPROVED")]))     # the gap #135 closes
        self.assertTrue(self.d("C", [rv("faris", "APPROVED")]))

    def test_approval_must_be_current(self):
        self.assertFalse(self.d("C", [rv("faris", "APPROVED", commit=OLD)]))
        self.assertFalse(self.d("C", [rv("faris", "APPROVED", stale=True)]))
        self.assertFalse(self.d("C", [rv("faris", "APPROVED", dismissed=True)]))

    def test_sole_approver_is_author(self):
        merge, why = mb.decide(pr(author="faris"), "C", True, [])
        self.assertFalse(merge)
        self.assertIn("only faris merges it", why)

    def test_author_cannot_approve_own(self):
        self.assertFalse(self.d("B", [rv("oc-rev", "APPROVED")], p=pr(author="oc-rev")))
        self.assertFalse(self.d("C", [rv("faris", "APPROVED")], p=pr(author="faris")))

    def test_request_changes_blocks_until_reapproved(self):
        self.assertFalse(self.d("A", [rv("oc-rev", "REQUEST_CHANGES")]))
        self.assertFalse(self.d("C", [rv("faris", "APPROVED", i=1), rv("oc-rev", "REQUEST_CHANGES", i=2)]))
        self.assertTrue(self.d("B", [rv("oc-rev", "REQUEST_CHANGES", i=1), rv("oc-rev", "APPROVED", i=2)]))
        self.assertTrue(self.d("A", [rv("oc-rev", "REQUEST_CHANGES", dismissed=True)]))

    def test_later_comment_does_not_cancel_approval(self):
        self.assertTrue(self.d("C", [rv("faris", "APPROVED", i=1), rv("faris", "COMMENT", i=2)]))

    def test_later_request_changes_cancels_own_approval(self):
        self.assertFalse(self.d("C", [rv("faris", "APPROVED", i=1), rv("faris", "REQUEST_CHANGES", i=2)]))


class Refresh(unittest.TestCase):
    def test_stale_pr_refreshed(self):
        self.assertTrue(mb.needs_refresh({**pr(), "merge_base": OLD}, SHA))

    def test_current_pr_not_refreshed(self):
        self.assertFalse(mb.needs_refresh({**pr(), "merge_base": SHA}, SHA))

    def test_wip_draft_and_unknown_not_refreshed(self):
        self.assertFalse(mb.needs_refresh({**pr(title="WIP: x"), "merge_base": OLD}, SHA))
        self.assertFalse(mb.needs_refresh({**pr(labels=["wip"]), "merge_base": OLD}, SHA))
        self.assertFalse(mb.needs_refresh({**pr(draft=True), "merge_base": OLD}, SHA))
        self.assertFalse(mb.needs_refresh({**pr(), "merge_base": OLD}, ""))      # infra: no main sha = never
        self.assertFalse(mb.needs_refresh(pr(), SHA))                            # no merge_base field


class Check(unittest.TestCase):
    def test_actions_status(self):
        self.assertTrue(mb.check_ok(status(), REPO))

    def test_token_posted_status_rejected(self):
        self.assertFalse(mb.check_ok(status(creator={"login": "oc-rev"}), REPO))

    def test_pr_branch_workflow_context_rejected(self):
        self.assertFalse(mb.check_ok(status(context="pr / plan (pull_request)"), REPO))

    def test_other_repo_run_rejected(self):
        self.assertFalse(mb.check_ok(status(url="/homelab/homelab-apps/actions/runs/1/jobs/1"), REPO))

    def test_not_success(self):
        for s in ("pending", "failure", "error"):
            self.assertFalse(mb.check_ok(status(state=s), REPO))

    def test_missing(self):
        self.assertFalse(mb.check_ok({"statuses": []}, REPO))
        self.assertFalse(mb.check_ok(None, REPO))


class ReadClass(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.plans = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, sha, cls):
        os.makedirs(os.path.join(self.plans, sha), exist_ok=True)
        with open(os.path.join(self.plans, sha, "class.json"), "w") as f:
            json.dump({"class": cls}, f)

    def test_reads_class(self):
        self.put(SHA, "B")
        self.assertEqual(mb.read_class(self.plans, SHA), "B")

    def test_missing(self):
        self.assertIsNone(mb.read_class(self.plans, SHA))

    def test_apps_prefix_is_separate(self):
        self.put(SHA, "A")                                   # an infra class never counts for apps, and back
        self.assertIsNone(mb.read_class(self.plans, SHA, "apps-"))
        self.put("apps-" + OLD, "B")
        self.assertEqual(mb.read_class(self.plans, OLD, "apps-"), "B")
        self.assertIsNone(mb.read_class(self.plans, OLD))

    def test_bad_sha_no_traversal(self):
        self.put(SHA, "A")
        for s in ("../" + SHA, SHA[:39], SHA.upper(), "", SHA + "/"):
            self.assertIsNone(mb.read_class(self.plans, s))

    def test_symlink_file_rejected(self):
        self.put(OLD, "A")
        os.makedirs(os.path.join(self.plans, SHA))
        os.symlink(os.path.join(self.plans, OLD, "class.json"), os.path.join(self.plans, SHA, "class.json"))
        self.assertIsNone(mb.read_class(self.plans, SHA))

    def test_symlink_dir_rejected(self):
        self.put(OLD, "A")
        os.symlink(os.path.join(self.plans, OLD), os.path.join(self.plans, SHA))
        self.assertIsNone(mb.read_class(self.plans, SHA))

    def test_garbage(self):
        os.makedirs(os.path.join(self.plans, SHA))
        with open(os.path.join(self.plans, SHA, "class.json"), "w") as f:
            f.write("not json")
        self.assertIsNone(mb.read_class(self.plans, SHA))


class Run(unittest.TestCase):
    """run() against a fake Gitea: merges only what decide() allows, with head_commit_id."""
    def test_run(self):
        with tempfile.TemporaryDirectory() as plans:
            for sha, cls in (("1" * 40, "A"), ("2" * 40, "C"), ("3" * 40, "C")):
                os.makedirs(os.path.join(plans, sha))
                with open(os.path.join(plans, sha, "class.json"), "w") as f:
                    json.dump({"class": cls}, f)
            prs = [{"number": n, "title": "x", "user": {"login": "oc-infra"}, "labels": [], "head": {"sha": str(n) * 40}}
                   for n in (1, 2, 3, 4)]                                    # 4 has no class.json
            reviews = {1: [], 2: [rv("oc-rev", "APPROVED", "2" * 40)], 3: [rv("faris", "APPROVED", "3" * 40)], 4: []}
            calls = []

            class Fake(mb.Gitea):
                def api(self, method, path, body=None):
                    calls.append((method, path, body))
                    if path.startswith("/pulls?"):
                        return prs
                    if path.startswith("/commits/"):
                        return status()
                    if path.endswith("/reviews"):
                        return reviews[int(path.split("/")[2])]
                    return None
            orig, mb.Gitea = mb.Gitea, Fake
            try:
                mb.run(REPO, plans, "", "t", dry=False)
            finally:
                mb.Gitea = orig
            merged = [(p, b["head_commit_id"]) for m, p, b in calls if m == "POST" and p.endswith("/merge")]
            self.assertEqual(merged, [("/pulls/1/merge", "1" * 40), ("/pulls/3/merge", "3" * 40)])


class RunRefresh(unittest.TestCase):
    def go(self, repo):
        calls = []
        stale = {"number": 9, "title": "x", "user": {"login": "faris"}, "labels": [], "head": {"sha": "9" * 40}, "merge_base": OLD}

        class Fake(mb.Gitea):
            def api(self, method, path, body=None):
                calls.append((method, path))
                if path == "/branches/main":
                    return {"commit": {"id": SHA}}
                if path.startswith("/pulls?"):
                    return [stale]
                if path.startswith("/commits/"):
                    return status(url=f"/{repo}/actions/runs/1/jobs/1")
                return []
        orig, mb.Gitea = mb.Gitea, Fake
        try:
            with tempfile.TemporaryDirectory() as plans:
                mb.run(repo, plans, "", "t", dry=False)
        finally:
            mb.Gitea = orig
        return calls

    def test_apps_updates_stale(self):
        self.assertIn(("POST", "/pulls/9/update?style=merge"), self.go("homelab/homelab-apps"))

    def test_infra_never_updates(self):
        calls = self.go("homelab/homelab-infra")
        self.assertFalse(any("/update" in p for m, p in calls))
        self.assertNotIn(("GET", "/branches/main"), calls)


if __name__ == "__main__":
    unittest.main(verbosity=1)
