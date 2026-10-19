# 6. Migration, risks, open questions

## 6.1 Steps

Reversible until step 8. **Until step 8, AdGuard and k3s are deployed by infra only; the apps pipeline renders and
runs check-mode from main against live hosts, never applies.** VM 210 (`hm1-dmz-app01`) is in no deployment and is
never touched (Faris 2026-10-19).

| # | Step | Where | Owner | Class | Done when |
|---|---|---|---|---|---|
| 0 | **Backups:** vzdump (snapshot mode, zstd) of 310–314, 501, 502, 601, 602 to `dir-import01`; list on the tracking issue | PVE (root) | Claude for Faris, SRE checks | — | 9 archives listed, sizes sane |
| 1 | Proposal agreed (6 × agree) and approved | infra #125 | PM, Faris | — | PR #124 approved |
| 2 | OpenClaw: `repo="apps"` everywhere, scopes, `comment_issue` limit, `list_catalog`/`app_status` stubs | openclaw | Claude drafts, Faris merges | — | agents can read/PR in apps |
| 3 | Standards: `flows.yaml` schema; `ci-apps` policy/AppRole (per-app lines), `secrets.yaml`, `svc-apps@pve`, lint `--repo apps`, conventions, probe flows | standards | oc-infra, oc-net; Faris merges | C | merged |
| 4 | Root setup: repo + protection + labels + bot `ci-apps`; runner `host-apps` (user `runner-apps`, group `devops-locks`, SSH key, AppRole files); OpenBao `apply.sh`; Proxmox identity + ACLs; test: login works, a `main` workflow runs on `host-apps`, **cross-repo dispatch from infra succeeds** | Gitea, CT 300, OpenBao, PVE | Claude as root for Faris | — | all four tests green |
| 4b | Infra locks (behaviour-neutral): `lock_hosts` in `ci-lib.sh`; `ci-apply.sh`, `ci-patch.sh` (`patch_run`), `ci-verify.sh`; job timeouts; `ci-apps.pub` key in `ansible/keys` | infra | oc-infra | C | applies/patch scan behave as before |
| 5 | Apps skeleton: README, docs, `_example`, `catalog.py`, `classify.py` + unit tests, lint gate, workflows (pr offline; deploy/verify **check mode**) | apps | oc-infra (pipeline), oc-cfg (docs, `_example`, playbooks) | C | pr green on a docs PR; classifier tests pass |
| 6 | ~~stg canary for AdGuard~~ **deferred** with Q10/Q11 (Faris 2026-10-19); skeleton ships `environments.yaml` and `deployments/stg/` empty | — | — | — | — |
| 7 | AdGuard entry + deployments wifi, iot (copy; nothing removed in infra). Make read-only tasks `check_mode: false` / `changed_when` where needed; record known check-mode exceptions | apps | oc-cfg | C | check-mode from main on 501/502/601/602 = 0 changed (exceptions listed); render + `adguard-drift.py` diff clean; L4 from apps green |
| 8 | k3s entry + `deployments/shr/k3s.yaml` + k3s L4 | apps | oc-cfg | C | check-mode on 310–314 = 0 changed; version/unit-file compare equal; k3s L4 green |
| 9 | Infra prep: dispatch code (`APPS_DISPATCH=0`), `patch_apply` health from the catalogue (apps clone, fallback, `patch_bundle_allow` guard kept), `reboot.yml` workflow, `reboot_guest` tool | infra, openclaw | oc-infra; Claude drafts tool | C | merged; no behaviour change |
| 10 | **Cutover evening** (Faris informed; bump PRs frozen in both repos): (a) `pre-cutover` snapshot + `k3s etcd-snapshot save`; (b) apps deploy check → apply, two consecutive real runs, second `changed=0`; (c) infra PR removes app plays/roles/files, `tests/apps/`, L4 from `verify.py` + `ansible/roles/verify`, `tests-check.py` app part, app docs, `changed.py` apps rule, `ci-apply` app policy lines; L4 starts in apps 03:15 in the same change set; (d) **separate PR after (c) is green**: `APPS_DISPATCH=1` | apps, infra, standards | oc-infra, oc-cfg; Claude merges for Faris | C | 03:00 + 03:15 green next night |
| 11 | OpenClaw remaining tools (`plan_from_catalog`, `verify_app`, `redeploy_app`), prompts, skills, runbooks | openclaw | Claude drafts, Faris merges | — | PM dry-run `plan_from_catalog` for a stg app |
| 12 | Probes live: blackbox, `file_sd`, alerts, inhibit; UDM policy for wifi/iot (Faris) | openclaw, standards, UniFi | oc-net, Faris | C | stg drill alerts within 5 min |
| 13 | Docs: infra README/docs links, local `architecture-apps.md`, runbook rows; close tracking issues | all | engineers, Claude | A | — |

**Revert trigger at cutover:** L4 or probes not green within **30 min** of 10(b), or any guest unhealthy.
**Revert order:** (1) apps `deploy` back to check mode; (2) revert the infra removal PR (re-applies as `ansible_all`,
restores `tests/`); (3) k3s control nodes restored only as a set after `etcd-snapshot`, never one alone; (4) vzdump
from step 0 as last resort.

## 6.2 Risks

| Risk | Mitigation |
|---|---|
| Two repos configure the same host during migration | apps check-only until 10(b); `APPS_DISPATCH` enabled separately (10d) |
| PR code reaching guests or secrets | offline PR job on `host-plan`; lint gate; deploy guard; separate runner user/key/AppRole |
| Write access on apps used to run branch workflows | same exposure as infra today (any writer can push a branch with its own workflow); mitigated by agent tools' path scopes and API-only writes; follow-up: runner allow-list per repo/branch when Gitea supports it |
| Check-mode false diffs | locks first (4b); known exceptions recorded; second proof (render/drift, version compare); two real idempotent runs at cutover |
| Guest green in infra, app broken | deploy report on the infra PR; `app-verify` issue; stg auto-rollback; probes |
| Cross-repo order mistakes | tracking issue with PR checklist; `plan_from_catalog` lists PRs in order; missing host = warning |
| Locks: long waits, deadlock | sorted, up front, 60-min wait, job timeout 240 min, loud failure |
| Host RAM (live 2026-10-19: 34.8 of 39 GB assigned; Sophos 12 GB planned) | stg canary adds 1 GB; `plan_from_catalog` counts planned guests |

## 6.3 Open questions for Faris (recommendation first)

| Q | Question | Recommendation |
|---|---|---|
| Q1 | Runner | decided: separate `host-apps` / `runner-apps` (reviewer, security) |
| Q2 | Pre-deploy snapshots | yes, snapshot-only `svc-apps@pve` |
| Q3 | Shared scripts | from the read-only infra clone |
| Q4 | L4 failures | `app-verify: <app>/<host>` in homelab-apps |
| Q5 | Version bump class | patch/minor B, major C, k3s always C |
| Q6 | Kubernetes workloads | later, `clusters/<zone>` |
| Q7 | `base_admin_user` | stays in infra |
| Q8 | Soak `beta` → `stable` | 7 days green in stg |
| Q9 | Guest metrics | later, if wanted |
| Q10 | k3s has no stg canary (RAM) | **deferred** by Faris (2026-10-19); missing canary = warning |
| Q11 | stg AdGuard canary (1 GB RAM) | **deferred** by Faris (2026-10-19) |
