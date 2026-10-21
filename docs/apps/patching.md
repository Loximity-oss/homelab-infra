# Patching, ring 1 - design note and runbook (Configuration Engineer)

Policy: skill `patching`. Faris approved the build 2026-10-18.

## Rings
| Ring | Guests | Handling |
|---|---|---|
| `ring0` | protected / manual: CT 100, CT 300, CT 303 (none are in the specs) | never patched by the team; unattended security updates 03:00, no reboot |
| `ring1` | every spec with `patch: ring1` (the inventory group `patch_ring1` is generated from the specs by `scripts/inventory.py`; nothing to maintain) | Sev1/2 automatic; Sev3/4 in the monthly bundle |
| `ring2` | none yet | scanned only |

Ring-1 guests today (10): hm1-dmz-app01 (210, Fedora), hm1-iot-dns01/02 (601/602), hm1-wifi-dns01/02 (501/502), hm1-shr-k3sc01..03 (310-312), hm1-shr-k3sw01..02 (313-314). Host hm1-mgt-pve01 is not covered here (svc-patcher, later).

## Parts
| Part | File | Owner |
|---|---|---|
| Scan (read-only) | `ansible/playbooks/patch-scan.yml`, role `patch_scan` | Configuration Engineer |
| Apply (never reboots) | `ansible/playbooks/patch.yml`, role `patch_apply` | Configuration Engineer |
| Schedule, snapshot, bundle PR, reboot issue | workflows / scripts | Infrastructure Engineer (see below) |

## Severity
`patch_scan/files/classify.py` gives every pending package Sev1-4.
- Ubuntu: the highest Ubuntu CVE priority (api `ubuntu.com/security/cves/<CVE>.json`) over the CVEs in the new changelog entries. critical = Sev1, high = Sev2, medium = Sev3, low/negligible = Sev4.
- Fedora: advisory severity from `dnf updateinfo`. Critical = Sev1, Important = Sev2, Moderate = Sev3, Low = Sev4.
- Not a security update = Sev4.
- **Fail safe:** a security update whose severity cannot be resolved (no CVE in the changelog, feed unreachable) is Sev3 and is never auto-applied.

Result per host on the runner: `$PATCH_OUT/<host>.scan.json` (counts, packages, basis, reboot hint).

## What `patch_apply` installs (patch_max_sev and patch_bundle_allow)
| Run | Variables | Applied per host |
|---|---|---|
| 6-hourly Sev1/2 | `patch_max_sev=2`, `patch_bundle_allow` undefined | packages with severity <= 2 (unchanged) |
| Monthly bundle | `patch_max_sev=4`, `patch_bundle_allow` = approved list (`-e @allow.json`) | Sev1/2 always, plus Sev3/4 **only if the package is on that host's list** |

`patch_bundle_allow` is a dict `host -> [package names]` that Faris approved in the bundle PR. Rules:
- A Sev3/4 package that is not on the host's list is **not** applied, even though `patch_max_sev=4`. This covers packages released or reclassified after the approval; they wait for the next bundle.
- A host missing from the dict gets Sev1/2 only. An empty list likewise.
- A malformed value (not a dict of lists) fails the play before anything is installed.
- With `patch_bundle_allow` undefined nothing changes: severity <= `patch_max_sev`.
- All refusals stay: protected vmids (100/300/303), ring0, not ring1, snapshot name `pre-patch_<date>_<time>`, `patch_max_sev` 1-4.
- `<host>.apply.json` reports `applied`, `deferred` (every scanned package not applied, so unapproved Sev3/4 are visible) and `bundle_mode`.

## Flow
1. **Every 6 h:** `patch-scan.yml` (all pipeline guests except ring0). Nothing changes on the guests except the package index refresh.
2. **Sev1/2 on ring 1:** pipeline takes snapshot `pre-patch_<YYYYmmdd>_<HHMM>` on each host with Sev1/2 packages, then `patch.yml -e patch_max_sev=2 -e patch_snapshot=<name>`. One host at a time, sorted, `any_errors_fatal` (a DNS pair or the k3s quorum is never down together). Only the classified packages are upgraded (`only_upgrade`).
3. **Verify after:** the `verify` role (L3 baseline, same as the nightly) and the app health from the homelab-apps catalogue (`patch_apps_health`) and a before/after comparison of failed systemd units. A new failed unit fails the play and stops the rollout; SRE restores with the snapshot.
4. **Monthly bundle, 25th:** the pipeline opens a PR with the Sev3/4 list per host (from the scans) and release notes. Faris approves in Gitea. Applied the last Saturday 02:00 MYT with `-e patch_max_sev=4 -e @allow.json` (`patch_bundle_allow` from the approved PR), again snapshot first and verify after.
5. **Reboots:** `patch.yml` never reboots. If `reboot_pending` (or the hint) is true in `<host>.apply.json`, the pipeline opens or comments a Gitea issue `patch: reboot <host>` with the package summary and release notes. It stays pending until Faris answers, no timeout. Rule when he approves: one host at a time; k3s control nodes drained first, wait for `Ready` and healthy etcd before the next, never two control nodes at once, workers one at a time (docs/apps/k3s.md).
6. **Snapshots:** pruned like `pre-apply_*` (7 days).

Protected guests: `patch_apply` asserts vmid not in 100/300/303, not in ring0, and in ring1. They have no snapshots or patching from the team.

## Needed from the Infrastructure Engineer (pipeline, Class C)
1. Scheduled workflow every 6 h: runs `patch-scan.yml` with `PATCH_OUT` set, keeps the scan JSON for later steps; opens/updates the reboot issues and the monthly bundle PR from it.
2. Sev1/2 apply job: for hosts with `counts.sev1 + counts.sev2 > 0`, snapshot, then `patch.yml -e patch_max_sev=2 -e patch_snapshot=...` (`--limit` those hosts), result issue/comment, failures to `pipeline.failed` for the SRE.
3. `scripts/snapshot.py`: accept the `pre-patch_` prefix next to `pre-apply_` (it hard-requires `pre-apply_`), plus prune for it. Rollback on a failed patch stays the SRE's call.
4. Monthly job (25th) for the bundle PR and last-Saturday 02:00 MYT apply (`patch_max_sev=4 -e @allow.json`, `patch_bundle_allow` generated from the approved PR) gated on the approved PR.
5. Runner needs: outbound HTTPS to ubuntu.com (CVE feed) and, from the guests, `changelogs.ubuntu.com`; python3 with `-I` on the runner (present).

## Open points
- Untested on the real flow: the Fedora scan (hm1-dmz-app01 SSH host key changed, ssh_exec refused it; the VM was probably rebuilt, known_hosts on the agent host needs refreshing). The Ubuntu `apt-get -s` parsing was checked by hand on hm1-shr-k3sw01.
- `patch_bundle_allow` logic is not yet run against a real allow.json; first bundle run should be checked by SRE (`applied` vs `deferred`).
- Reboot playbook (drain for k3s, health wait) is not part of this PR; it follows after the first reboot decision.
- k3s itself (binary) is not patched by apt; upgrade by the pinned version (docs/apps/k3s.md).
