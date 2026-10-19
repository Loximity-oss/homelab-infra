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
| 4 | Root setup: repo + protection (`main` + rule `*`: push only `oc-cfg`, `oc-infra`, `faris`) + labels; bot `ci-apps` (write apps, push-blocked; read + comment infra) and its three tokens; infra `gitea_ci_token` read on apps; runner `host-apps` (user `runner-apps`, SSH key, AppRole files); group `devops-locks`, `/var/lib/devops/locks` `root:devops-locks` **3770**, lock files pre-created `0664`; OpenBao `apply.sh` (per-app policies, token roles); Proxmox identity + pool ACLs; tests: AppRole login, child token per app (adguard denied on k3s), a `main` workflow on `host-apps`, **cross-repo dispatch from infra**, `ci-apps` push refused | Gitea, CT 300, OpenBao, PVE | Claude as root for Faris | — | all four tests green |
| 4b | Infra locks (behaviour-neutral): `lock_hosts` in `ci-lib.sh`; `ci-apply.sh` (locks before `SNAP_HOSTS`, released in `finish()`), `ci-patch.sh` (`patch_run`), `ci-verify.sh`; job timeouts; `ci-apps.pub` in `ansible/keys` + deployment-host-only install in `base_common` | infra | oc-infra | C | applies/patch scan behave as before, **incl. one apply that waits on a held lock** (infra 3.3-2) |
| 5 | Apps skeleton: README, docs, `_example`, `environments.yaml`, `catalog.py`, `classify.py`, lint gate, `bao_create_secret`, `deploy.yml` (guard, precedence), workflows (pr offline; deploy/verify **check mode**). **Unit tests:** classifier rows with real paths incl. braces and `meta/**`; lint gate (each banned construct, `src:`/`include_*` escape, FQCN/`query`/`with_<lookup>`, `__`/`mro` per spelling, `.j2` under `catalog/adguard/files/sub/x.j2`, `delegate_to` to another deployment host allowed and to `localhost` rejected, `check_mode: false` rules, `set_fact` on banned keys); Jinja in deployment values; banned var keys; precedence (deployment var cannot override `zone`/`vmid`/`role`); guard (protected VMID, `ansible_host` in 192.168.67.0/29); per-app token isolation. `requirements.yml` lists `community.general` (k3s `modprobe`) | apps | oc-infra (pipeline), oc-cfg (docs, `_example`, playbooks) | C | pr green on a docs PR; classifier tests pass |
| 6 | ~~stg canary for AdGuard~~ **deferred** with Q10/Q11 (Faris 2026-10-19); skeleton ships `environments.yaml` and `deployments/stg/` empty | — | — | — | — |
| 7 | AdGuard entry + deployments wifi (wave 2), iot (wave 3) (copy; nothing removed in infra). Role: password + secret write via `bao_create_secret`, files via `{{ role_path }}/../files`, `probe.hm1` rewrite created before the first healthcheck; L4 adds the rewrite + peer check. Read-only tasks (`--version`, drift script, `--check-config`) get `check_mode: false` + `changed_when: false` per 5.1; known check-mode exceptions recorded: the `.managed` rewrite and the `--version`-gated download; no `app_adguard_*` default may still reference `192.168.70.12:8200` or `/etc/openbao/ca.crt` | apps | oc-cfg | C | check-mode from main on 501/502/601/602 = **no live-file or service change** (the `.managed` rewrite and listed exceptions ignored, cfg R3); render + `adguard-drift.py` diff clean; L4 from apps green |
| 7a | Infra no-op PR touching `ansible/` (Class B) → `ansible_all` re-apply installs `ci-apps.pub` on the hosts now listed in deployments (infra 3.3-1); repeated after step 8 for 310–314 | infra | oc-infra | B | `runner-apps` SSH to 501/502/601/602 works; only then the step 7 check-mode run |
| 8 | k3s entry + `deployments/shr/k3s.yaml` (`api_vip`, `vrrp_router_id`, taints as plain data) + k3s L4; escrow via `bao_create_secret`; cross-node token reads keep `delegate_to` to the init host (allowed: same deployment); the `localhost` VIP `/livez` task moves to `health`/`probes` (cfg 3.2-B1) | apps | oc-cfg | C | check-mode on 310–314 = 0 changed; version/unit-file compare equal; k3s L4 green |
| 9 | Infra prep: dispatch code (`APPS_DISPATCH=0`), `patch_apply` health from the catalogue (apps clone, fallback, `patch_bundle_allow` guard kept), `reboot.yml` workflow, `reboot_guest` tool | infra, openclaw | oc-infra; Claude drafts tool | C | merged; no behaviour change |
| 10 | **Cutover evening** (Faris informed; bump PRs frozen in both repos): (a) `pre-cutover` snapshot + `k3s etcd-snapshot save`; (b) apps deploy check → apply, two consecutive real runs, second `changed=0`; (c) infra PR removes app plays/roles/files, `tests/apps/`, L4 from `verify.py` + `ansible/roles/verify`, `tests-check.py` app part, app docs, `changed.py` apps rule, `ci-apply` app policy lines; L4 starts in apps 03:15 in the same change set; (d) **separate PR after (c) is green**: `APPS_DISPATCH=1` | apps, infra, standards | oc-infra, oc-cfg; Claude merges for Faris | C | 03:00 + 03:15 green next night |
| 11 | OpenClaw remaining tools (`plan_from_catalog`, `verify_app`, `redeploy_app`), prompts, skills, runbooks | openclaw | Claude drafts, Faris merges | — | PM dry-run `plan_from_catalog` for a stg app |
| 12 | Probes live: blackbox, `file_sd`, alerts, inhibit; three UDM policies for wifi/iot (Faris), confirmed by oc-net (`state: implemented`) before gateway targets are emitted | openclaw, standards, UniFi | oc-net, Faris | C | drill on hm1-wifi-dns02 (4.7): `ProbeFailed` ≤ 5 min, `DNSQueryFailed` silent |
| 13 | Docs: infra README/docs links, local `architecture-apps.md`, runbook rows; close tracking issues | all | engineers, Claude | A | — |

**Revert trigger at cutover:** L4 or probes not green within **30 min** of 10(b) — `DNSQueryFailed` and `ProbeFailed` must be clear and **not silenced** in that window (SRE R3-6) — or any guest unhealthy.
**Revert order:** (0) set `APPS_DISPATCH=0` (revert 10d) so the revert's `ansible_all` apply dispatches nothing (infra R3-3); (1) apps `deploy` back to check mode; (2) revert the infra removal PR (re-applies as `ansible_all`,
restores `tests/`); (3) k3s control nodes restored only as a set after `etcd-snapshot`, never one alone; (4) vzdump
from step 0 as last resort.

## 6.2 Risks

| Risk | Mitigation |
|---|---|
| Two repos configure the same host during migration | apps check-only until 10(b); `APPS_DISPATCH` enabled separately (10d) |
| PR code reaching guests or secrets | offline PR job on `host-plan`; lint gate; deploy guard; separate runner user/key/AppRole |
| Write access on apps used to run branch workflows | **closed for apps**: no agent may write `.gitea/**`, `ci-apps` cannot push (rule `*`), `deploy` runs only on `main`, `pr` uses `pull_request_target` (2.1). **Infra still has this exposure** (oc-infra writes `.gitea/*` there): Q12 |
| Check-mode false diffs | locks first (4b); known exceptions recorded; second proof (render/drift, version compare); two real idempotent runs at cutover |
| Guest green in infra, app broken | deploy report on the infra PR; `app-verify` issue; stg auto-rollback; probes |
| Cross-repo order mistakes | tracking issue with PR checklist; `plan_from_catalog` lists PRs in order; missing host = warning |
| Locks: long waits, deadlock | sorted, up front, 60-min wait, job timeout 240 min, loud failure |
| Host RAM (live 2026-10-19: 34.8 of 39 GB assigned; Sophos 12 GB planned) | no new guests in this migration (stg canary deferred); `plan_from_catalog` counts planned guests |

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
| Q12 | infra: oc-infra can write `.gitea/*` (a branch workflow could run on `host-apply`) | follow-up after this migration, tracked in its own infra issue (reviewer 3.2-h) |
