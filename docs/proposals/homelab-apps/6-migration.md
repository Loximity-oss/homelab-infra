# 6. Migration, risks, open questions

## 6.1 Steps

Each step is one PR or one root action, reversible until step 8. **Until step 8, AdGuard and k3s are still deployed
by infra; the apps pipeline only runs `--check --diff`.**

| # | Step | Where | Owner | Class | Done when |
|---|---|---|---|---|---|
| 1 | Proposal agreed (6 verdicts "agree") and approved | infra #125 | PM, Faris | — | Faris approves PR #124 |
| 2 | Standards: `ci-apps` AppRole/policy, `svc-apps@pve` + role, lint `--repo apps`, conventions, probe flows | standards | oc-infra, oc-net, Faris | C | merged |
| 3 | Root: create repo + protection + labels; OpenBao `apply.sh`; Proxmox identity + ACLs; `secret_id` for `runner-apply`; secret `hm1/platform/ci/apps`; verify the global runners pick up the new repo | Gitea, PVE, OpenBao | Faris (or Claude as root on request) | — | AppRole login works from CT 300; a test workflow runs |
| 4 | Apps skeleton: README, docs (orchestration, patching, verification, adding-an-app, catalog index), `_example`, `catalog.py` checks, `classify.py`, workflows **check mode only** | apps | oc-infra (pipeline), oc-cfg (docs, `_example`) | C | `pr` green on a docs PR; classifier unit tests pass |
| 5 | AdGuard entry + `deployments/wifi`, `deployments/iot` (copy, nothing removed in infra) | apps | oc-cfg | C | check-mode deploy on 501/502/601/602 = **0 changed**; L4 from apps green |
| 6 | k3s entry + `deployments/shr/k3s.yaml` + new k3s L4 (4.5) | apps | oc-cfg | C | check-mode on 310–314 = 0 changed; k3s L4 green |
| 7 | Infra prep, all behind flags/no behaviour change: host locks (`ci-apply.sh`, `ci-patch.sh`, `ci-verify.sh`), dispatch after the tests block (`APPS_DISPATCH=0`), `patch_apply` health from the catalogue, reboot-policy reader | infra | oc-infra | C | merged; a no-op apply and a patch scan behave as before |
| 8 | **Cutover evening** (Faris present): (a) apps `deploy` check → apply; (b) infra removes app plays/roles/files, `tests/apps/`, L4 from `verify.py`, `ansible/roles/verify` L4 tasks, `tests-check.py` app part, `docs/apps/` app docs, `changed.py` apps rule; sets `APPS_DISPATCH=1`; (c) standards drops `apps/*` from `ci-apply` | apps, infra, standards | oc-infra, oc-cfg; Faris merges | C | full apps deploy = 0 changes; no-op infra apply green; next 03:00 + 03:15 green |
| 9 | OpenClaw: repos, scopes, tools, poller, scan, dashboard, prompts | openclaw | Faris (Claude drafts) | — | PM runs `list_catalog`, `plan_from_catalog` dry-run, `verify_app` |
| 10 | Probes live: blackbox exporter, `file_sd` sync, alerts | openclaw + standards flows | Faris / oc-net | — | a stopped AdGuard (stg drill) alerts within 3 min |
| 11 | Docs: infra README/docs links, local AIOps docs (`architecture-apps.md`), runbook rows | all | oc-infra, oc-cfg, Claude | A | — |

**Rollback:** before step 8, close the apps PRs (nothing changed). Step 8: revert the infra removal PR (one Class C
PR) and set apps `deploy` back to check mode.

## 6.2 Risks

| Risk | Mitigation |
|---|---|
| Two repos configure the same host during migration | apps check-only until the cutover; cutover is one PR set in one window |
| Inventory changes under the apps pipeline | reads infra main at run time, records the sha; infra dispatches only after its own merge |
| Guest green in infra, app broken | deploy result commented on the infra PR; `app-verify` issue; stg auto-rollback |
| Lost single-PR atomicity (guest + app) | deployment-first is safe (missing hosts skipped); PM sequences; `plan_from_catalog` lists all PRs up front |
| Token / rights sprawl | separate `ci-apps` and snapshot-only `svc-apps@pve`; no DevOpsAdmin in apps |
| Deadlock or long waits on host locks | all locks sorted, up front, 60-min timeout, loud failure |
| Catalogue drift (entry changed, deployment pinned forever) | weekly version check lists pinned deployments; dashboard shows them |
| Probes need new flows from mgt | oc-net adds them to `flows.yaml` first (step 2); probes go live last (step 10) |
| Host RAM (live 2026-10-19: 34.8 GB assigned of 39 GB) | `plan_from_catalog` refuses builds that don't fit; Sophos VM (12 GB) still planned |

## 6.3 Open questions (recommendation first)

| Q | Question | Recommendation |
|---|---|---|
| Q1 | Runner for apps | reuse `host-apply` |
| Q2 | Pre-deploy snapshots | yes, with snapshot-only `svc-apps@pve` |
| Q3 | Shared scripts | from the read-only infra clone, sha recorded |
| Q4 | L4 failures | `app-verify: <app>/<host>` issues in homelab-apps |
| Q5 | Version bump class | patch/minor B, major C, k3s always C |
| Q6 | Kubernetes workloads | later: `clusters/<zone>` GitOps layout once a stg cluster exists |
| Q7 | `base_admin_user` | stays in infra (guest config) |
| Q8 | `beta` → `stable` soak | 7 days green in stg |
| Q9 | Guest metrics (`node_exporter`) | later, only if Faris wants dashboards |
