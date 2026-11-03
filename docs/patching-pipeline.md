# Ring-1 patching: pipeline side (Infrastructure Engineer)

Policy: skill `patching`. Ansible side (scan / apply roles, severity, verify): `docs/apps/patching.md`.
Faris approved the build 2026-10-18 (PR #114).

## Why this is Class C
It adds scheduled workflows that change guests without a PR (Sev1/2 packages), extends `scripts/snapshot.py`
(apply token) and `scripts/gitea.py`, and adds `patches/*` to the Class C paths in `policy/classify.py` so that
every monthly bundle PR needs Faris's approval. Nothing in `tests/` changes.

## Workflows (all on the `host-apply` runner, serialised by `/var/lib/devops/locks/patch.lock`)
| Workflow | When (MYT) | `scripts/ci-patch.sh` mode | What |
|---|---|---|---|
| `patch` | 09:40, 15:40, 21:40, 03:40 | `scan` | `patch-scan.yml` (all guests except ring0, read-only) -> ring-1 hosts with `counts.sev1 + counts.sev2 > 0`: snapshot `pre-patch_<YYYYmmdd>_<HHMM>`, then `patch.yml -e patch_max_sev=2 -e patch_snapshot=<name> --limit <hosts>`. Result comment on issue `patch: Sev1/2 applied <YYYY-MM>`. |
| `patch-bundle` | 25th 09:00 | `bundle` | fresh scan -> PR `patch(hm1): monthly bundle <YYYY-MM>` from `patch/bundle-<YYYY-MM>` with `patches/hm1/<YYYY-MM>.md` (packages per host, CVE / advisory links, reboot hints) and `.json` (machine-readable list). Class C. An existing PR from that branch is never pushed to again (would make an approval stale). |
| `patch-monthly` | every Saturday 02:00, acts on the last one | `monthly` | Only if the bundle PR is **merged with a current APPROVED review by `faris`** (`gitea.py pr-gate`). Snapshot, then `patch.yml -e patch_max_sev=4 -e @allow.json` where `allow.json` = `{patch_bundle_allow: {host: [package, ...]}}` from the approved bundle. Result comment on the bundle PR. Marked done per month. |

If the last Saturday falls on or before the 25th (possible in 30-day months and February), that month's bundle
is applied on the following month's last Saturday; the monthly job checks the current and the previous month.

## Safety
- **Never reboots.** `reboot_pending` in `<host>.apply.json` or `reboot_required` in the scan opens issue
  `patch: reboot <host>` (label `patch`): applied packages, still-pending reboot-hint packages, release-note links,
  and the k3s reboot rule. It stays open until Faris answers; no timeout. One comment per patch run that leaves a
  reboot pending (digest marker, no 6-hourly spam). It is closed only when a later scan reports no pending reboot.
- **Protected guests** 300, 301, 303 and templates 9000-9199: refused by `patch_plan.py` (protected.yaml + hard
  list), by `snapshot.py` (hard list), and by the `patch_apply` role. ring0 is excluded by the scan play and by
  `patch_plan.py` (only `patch: ring1` specs are ever patched).
- **Snapshot first:** a host whose snapshot failed is not patched (and the run fails).
- **Fail closed (monthly):** the job refuses to apply when `patch_apply` does not honour `patch_bundle_allow`;
  without it `patch_max_sev=4` would also apply Sev3/4 packages released after the bundle was approved.
- `pre-patch_*` snapshots are pruned after 7 days with `pre-apply_*` by the nightly `verify` workflow
  (`snapshot.py prune`, both prefixes by default). Rollback stays the SRE's call.

## Post-patch tests (L1-L3, Faris 2026-11-03)
After `patch.yml` in every patch run (Sev1/2 `scan` and `monthly`), `ci-patch.sh` runs `scripts/verify.py` (the
apply's test set: L1 platform incl. the protected-guest visibility row, L2 reachability, L3 Goss OS baseline) on
**every host handed to `patch.yml`** (= every host with a pre-patch snapshot), also when `patch.yml` failed, so the SRE
sees the state of the host the rollout stopped on. Before this, the only checks were inside `patch_apply` (L3
baseline + no new failed units + catalogue app health, per host, stopping the rollout at the first bad host); those
stay unchanged.
- Runs while the host locks of the patch run are still held (like the apply's tests); read-only token for L1.
- Results in `$PATCH_OUT/verify/` (`verify.json`, `verify.md`, `meta.json`) and `tests.txt`; the summary
  (`patch: Sev1/2 applied <YYYY-MM>` / bundle PR comment) gets a per-host table: patch result, pass/fail/skip,
  the first failed checks, and the rollback command with the pre-patch snapshot name for failed guests.
- A failed test, or no result at all, -> `patch: failed (<mode>)` with the per-host failures and the snapshot name
  -> exit 1 -> `pipeline.failed` -> SRE. **Never an automatic rollback, never a reboot**: rollback is the SRE's call.
- Monthly: a test failure does not block the `monthly-<M>.done` marker (the bundle was applied; re-applying it would
  not fix the test). A `patch.yml` failure still does, as before.
- Hosts with no apply result (rollout stopped) are tested too and labelled so; a failure there may predate the run.
  A failing L1 row that is not about patching (spec drift, protected guest visible) also fails the patch run.

## Report format (infra #206)
Package tables in `patch: Sev1/2 applied <YYYY-MM>`, `patch: reboot <host>` and the bundle `.md`: package,
`installed -> new`, Sev, basis, **CVE count**, the 3 highest-priority CVE / advisory links (Ubuntu priority from the
runner's `ubuntu-cve-cache.json`, then newest; Fedora advisory severity) and `+N more`. A body above 30000 bytes is
re-rendered with 1, then 0 links per package, and cut at a line as a last resort. Full lists: `<host>.scan.json` on the
runner and the bundle `.json`. Display only: Sev, selection, snapshots and reboots are unchanged.
Test: `python3 scripts/tests/test-patch-plan.py`.

## Failures
Any failure (including a failed post-patch test) exits 1 -> `pipeline.failed` (workflow `patch`, `patch-bundle` or
`patch-monthly`) -> SRE, plus an issue `patch: failed (<mode>)` with the log tail and the snapshot name. Run data is kept 35 days on the runner in
`/var/lib/devops/patch/<stamp>-<mode>/` (`latest` -> newest): `<host>.scan.json`, `<host>.apply.json`, logs.

## Needs outside this repo
- Runner (CT 300) egress HTTPS to `ubuntu.com` (CVE priority feed used by `classify.py`); guests egress to
  `changelogs.ubuntu.com` (`apt-get changelog`). Network Engineer (flows / firewall), not done here.
- Gitea label `patch` (issues are created without it if it is missing).
- The `ci` Gitea user must be able to push `patch/bundle-*` branches and open PRs.
