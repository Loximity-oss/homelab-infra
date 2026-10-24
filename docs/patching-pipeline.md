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
- **Protected guests** 100, 300, 301, 303 and templates 9000-9199: refused by `patch_plan.py` (protected.yaml + hard
  list), by `snapshot.py` (hard list), and by the `patch_apply` role. ring0 is excluded by the scan play and by
  `patch_plan.py` (only `patch: ring1` specs are ever patched).
- **Snapshot first:** a host whose snapshot failed is not patched (and the run fails).
- **Fail closed (monthly):** the job refuses to apply when `patch_apply` does not honour `patch_bundle_allow`;
  without it `patch_max_sev=4` would also apply Sev3/4 packages released after the bundle was approved.
- `pre-patch_*` snapshots are pruned after 7 days with `pre-apply_*` by the nightly `verify` workflow
  (`snapshot.py prune`, both prefixes by default). Rollback stays the SRE's call.

## Failures
Any failure exits 1 -> `pipeline.failed` (workflow `patch`, `patch-bundle` or `patch-monthly`) -> SRE, plus an
issue `patch: failed (<mode>)` with the log tail and the snapshot name. Run data is kept 35 days on the runner in
`/var/lib/devops/patch/<stamp>-<mode>/` (`latest` -> newest): `<host>.scan.json`, `<host>.apply.json`, logs.

## Needs outside this repo
- Runner (CT 300) egress HTTPS to `ubuntu.com` (CVE priority feed used by `classify.py`); guests egress to
  `changelogs.ubuntu.com` (`apt-get changelog`). Network Engineer (flows / firewall), not done here.
- Gitea label `patch` (issues are created without it if it is missing).
- The `ci` Gitea user must be able to push `patch/bundle-*` branches and open PRs.
