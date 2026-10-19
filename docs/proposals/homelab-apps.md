# Proposal: `homelab-apps` — the application layer gets its own repo

| | |
|---|---|
| Status | **WIP — team review** (2026-10-19). Nothing is built until every engineer has agreed and Faris approves. |
| Asked by | Faris, 2026-10-19: "anything managed by DevOps is in a repo; guest config stays in infra; apps like AdGuard go to `homelab-apps`; orchestration and patching of guests and apps documented." |
| Review | Discussion and the per-engineer verdicts are on the linked issue. This file is changed until all agree. |

## 0. Summary

1. **`homelab-infra` keeps the guest**: Terraform, templates, guest specs, OS baseline (`base_*`), OS patching, L1–L3 tests, host helpers.
2. **`homelab-apps` gets everything that runs *on* a guest for a purpose**: one folder per app with its Ansible role, config files, L4 tests, docs and an `app.yaml` manifest. First apps: AdGuard (`dns`) and k3s (`k3sc`, `k3sw`).
3. **The guest spec stays the single source of truth for hosts.** Apps select hosts by the spec's `role` code; `homelab-apps` reads the inventory from `homelab-infra` main and never defines hosts.
4. **Orchestration is one-way:** infra apply → (guest ready, L1–L3 green) → dispatches the apps `deploy` for those hosts. Apps never trigger infra. One per-host lock serialises apply, app deploy and patching.
5. **Patching is split by owner of the bits:** OS packages = infra rings (unchanged); app versions = pinned in `app.yaml`, bumped by PR after a weekly upstream check. Reboots stay Faris-approved; each app declares how its hosts are rebooted safely.
6. **Migration without a gap:** apps pipeline runs in check mode (`--check --diff`, must show 0 changes on live hosts) until a single cutover PR pair switches ownership.

## 1. Goals and non-goals

| Goals | Non-goals (now) |
|---|---|
| Clear ownership: guest vs application | Kubernetes workloads (Flux/Argo) — later decision, see Q6 |
| App changes can never touch Terraform or OS baseline | Changing OpenBao secret paths (they stay `<zone>/secret/apps/<app>`) |
| Each app is self-contained: role + config + tests + docs + manifest | New apps — this is a move, not a feature |
| Documented orchestration and patching for guests **and** apps | Changing guest specs or the zone model |
| Same gates (A/B/C), same reviewers, same evidence as today | Multi-site |

## 2. Boundary — what goes where

### 2.1 Rule

> **Guest config** = anything every guest of an OS gets, or that makes the guest exist, reachable, patched and testable → `homelab-infra`.
> **App** = anything selected by a guest's `role` code that makes the guest *do its job* → `homelab-apps`.

### 2.2 File moves (current → new)

| Today in `homelab-infra` | Moves to `homelab-apps` | Stays in infra |
|---|---|---|
| `ansible/roles/app_adguard/` | `apps/adguard/role/` | |
| `apps/adguard/*` (templates, `adguard-drift.py`) | `apps/adguard/files/` | |
| `ansible/playbooks/group_vars/role_dns.yml` | `apps/adguard/vars.yml` (zone/host overrides: `apps/adguard/vars/zone_<z>.yml`, `host_<h>.yml`) | |
| `tests/apps/dns.goss.yaml.j2` (L4) | `apps/adguard/tests/l4.goss.yaml.j2` | |
| `docs/apps/adguard.md`, `adguard-runbook.md` | `apps/adguard/docs/` | |
| `ansible/roles/svc_k3s/` | `apps/k3s/role/` | |
| `apps/k3s/*` | `apps/k3s/files/` | |
| `docs/apps/k3s.md`, `k3s-runbook.md` | `apps/k3s/docs/` | |
| `ansible/roles/app_example/`, `apps/_example/`, `docs/apps/_template.md` | `apps/_example/` (role, files, tests, docs, `app.yaml`) | |
| site.yml plays "AdGuard DNS", "k3s control plane/workers/escrow" | generated from `app.yaml` (`deploy.yml`) | site.yml keeps "Base layer" + "Admin user" |
| | | `ansible/roles/base_common`, `base_admin_user`, `patch_scan`, `patch_apply`, `verify` (L1–L3 part) |
| | | `tests/os/*` (L3), `scripts/verify.py` L1–L3, `ansible/keys/*` |
| | | `docs/apps/patching.md` → renamed `docs/patching-os.md`; `docs/apps/hm1-dmz-app01-admin.md`, `vm-templates.md` → `docs/guests/` |
| `scripts/changed.py` rule "apps/ → configure all hosts" | removed | |
| | | everything else (terraform, templates, host/, policy/, scripts/, .gitea/) |

`base_admin_user` (the app01 admin user) is guest config: stays in infra.

### 2.3 What else is "managed by DevOps" and where it lives (complete list)

| Thing | Repo | Notes |
|---|---|---|
| Guests, templates, pools/tags (via specs) | infra | unchanged |
| OS baseline, admin users, SSH keys | infra | unchanged |
| OS patching (scan, Sev1/2 auto, monthly bundle, reboot issues) | infra | health gates read `app.yaml` (5.3) |
| Host helpers on PVE (`pve-feature-broker`) | infra `host/` | unchanged |
| AdGuard, k3s, future host apps | **apps** | |
| App secrets (values) | OpenBao | paths unchanged; policy moves to a new `ci-apps` AppRole |
| Naming, zones, roles, OpenBao/Proxmox ACL definitions | standards | `roles.yaml` gains `app:` link (11) |
| UniFi / firewall | standards (`flows.yaml`) + Faris | unchanged |
| OpenClaw | openclaw | tool + prompt changes (10) |

## 3. `homelab-apps` layout and the app manifest

```
homelab-apps/
  README.md                     how an app is built, deployed, tested, patched (links docs/)
  apps/<name>/
    app.yaml                    manifest (below) — the only file the pipeline reads to decide anything
    role/                       Ansible role (tasks/, defaults/, handlers/, templates/)
    files/                      config templates, helper scripts copied by the role
    vars.yml  vars/             shared config (was group_vars/role_<x>.yml) + zone_/host_ overrides
    tests/l4.goss.yaml.j2       L4 app checks (Class C, like infra tests/)
    docs/design.md  docs/runbook.md
  apps/_example/                copy-me app (compose-based), fully filled in
  playbooks/deploy.yml          generic: loads app.yaml for each selected app, runs its role with its rollout rules
  scripts/                      ci-pr.sh, ci-deploy.sh, ci-verify.sh, ci-version-check.sh, manifest.py, classify.py (apps)
  policy/classify.py            A/B/C for this repo
  .gitea/workflows/             pr.yml, deploy.yml, verify.yml, version-check.yml
  docs/orchestration.md         end-to-end flows (section 4 of this proposal, kept current)
  docs/patching.md              guests + apps patching (section 5, kept current)
  docs/adding-an-app.md
```

### 3.1 `app.yaml` (schema, AdGuard filled in)

```yaml
name: adguard                      # = folder name
owner: oc-cfg
description: AdGuard Home DNS resolver for client zones
hosts:
  roles: [dns]                     # spec role codes (homelab-standards naming/roles.yaml); the ONLY host selector
  zones: []                        # optional filter; empty = every zone where the role exists
rollout:
  serial: 1                        # one host at a time
  order: sorted
  stop_on_failure: true
  groups: []                       # ordered sub-plays when roles differ (k3s: [{roles:[k3sc], vars:{svc_k3s_node_type: server}}, {roles:[k3sw], ...}])
version:                           # what "patching the app" means (section 5.2)
  pin: v0.107.79
  sha256: c48f4a43000665484c5ec28177de11a004759b620dae8f77b2aabefc9ef3687f
  source: github:AdguardTeam/AdGuardHome   # upstream for the weekly version check
  bump_class: B                    # B for patch/minor; major is always C; k3s sets C
secrets:                           # OpenBao paths the deploy may CREATE (never read back into Git)
  - "<zone>/secret/apps/adguard/<host>"
health:                            # used by OS patching and reboots as the per-host gate (5.3)
  services: [AdGuardHome]
  command: "dig +time=2 +tries=1 @127.0.0.1 gitea.com A | grep -q ."
  timeout_s: 60
reboot:                            # how the reboot run handles these hosts (5.4)
  policy: one_at_a_time            # one_at_a_time | pair (never both of a pair) | drain (k3s)
  before: []                       # e.g. k3s: drain command
  after_wait: health
tests: tests/l4.goss.yaml.j2
drift: { command: "/usr/local/sbin/hm1-adguard-drift ...", in_l4: true }
```

k3s example differences: `hosts.roles: [k3sc, k3sw]`, `rollout.groups` (servers first, `--cluster-init` on the first sorted control node, escrow play last), `reboot.policy: drain`, `before: ["kubectl drain {{host}} --ignore-daemonsets --delete-emptydir-data --timeout=300s"]`, `after: ["kubectl uncordon {{host}}"]`, `version.bump_class: C`.

`scripts/manifest.py check` (PR gate) validates: name = folder, every role code exists in standards `roles.yaml`, no two apps claim the same role code, secrets match `<zone>/secret/apps/<name>/...`, `health`, `reboot.policy` and `version` present.

## 4. Orchestration

### 4.1 Who triggers whom

```
                ┌────────────── homelab-infra ─────────────────┐        ┌──────────── homelab-apps ────────────┐
  PR merged ──► │ apply: Terraform → known_hosts → Ansible     │        │                                       │
                │ base (site.yml) → L1–L3 tests                │──────► │ deploy(hosts=<created/configured>)    │
                │   for hosts whose role has an app            │ dispatch│  → snapshot → role(s) → L4 tests     │
                └──────────────────────────────────────────────┘        │  → comment on the infra PR + own run │
                                                                         └───────────────────────────────────────┘
  apps PR merged ───────────────────────────────────────────────────────► deploy(app=<changed>, hosts=all of its roles)
```

- **One direction only.** Infra dispatches the apps `deploy` workflow (Gitea API `workflow_dispatch`, inputs `hosts`, `reason`, `infra_pr`). Apps never dispatches infra.
- **Inventory**: the apps pipeline clones `homelab-infra` main (read-only, `ci` token) and runs its `scripts/inventory.py`. Hosts come only from guest specs; protected guests have no spec, so they can never be targeted.
- **Lock**: `/var/lib/devops/locks/host-<hostname>.lock` (flock, on CT 300), taken by infra apply (Ansible part), apps deploy and patching, in that host order (sorted) — no deadlock, a waiting job times out after 60 min and fails loudly.

### 4.2 New guest that runs an app (e.g. a third DNS resolver)

| Step | Repo | Who | Gate |
|---|---|---|---|
| 1. App supports the role already? If not: add role code to `app.yaml` | apps | oc-cfg | B |
| 2. Guest spec with `role: dns` | infra | oc-infra | A (B in shr) |
| 3. Infra apply: create, base, L1–L3 | infra | pipeline | — |
| 4. Infra apply dispatches apps deploy for the new host | apps | pipeline | — |
| 5. App role runs, L4 tests; result commented on the infra PR | apps | pipeline | — |
| 6. Failure → `app-verify: adguard/<host>` issue (apps), PM routes to oc-cfg/SRE | apps | agents | — |

Order rule: **app first, guest second.** An app PR for a role with no hosts deploys to nobody (no-op), so merging it early is safe. This replaces today's "one WIP PR with guest + role".

### 4.3 App change (config, role, version)

`apps PR` → pr workflow: lint (yamllint, ansible-lint, manifest check, gitleaks), render templates for every target host (offline), `ansible-playbook --check --diff` against the live target hosts with the read-only path (no secrets created in check mode), classify, gate → merge → `deploy(app)`: snapshot `pre-deploy_<ts>` of target hosts → role with the app's rollout rules → L4 tests → PR comment. stg hosts auto-rollback on failed L4 (same rule as infra D3).

### 4.4 Guest change (OS baseline, size, template)

Infra only. If the change **re-creates** the guest (replace), infra apply dispatches apps deploy for it (same as 4.2 step 4). An in-place base change does not redeploy apps; nightly L4 catches side effects.

### 4.5 Role change or destroy

- Destroy: infra (Class C). The host disappears from inventory; apps needs nothing. App data on the guest disk is gone (backups: open item A-1 unchanged).
- Changing a guest's `role` code = rebuild (replace, Class C), so the old app never lingers. `manifest.py` refuses an app that would "un-deploy" itself.

### 4.6 Daily timeline (MYT)

| Time | Job | Repo |
|---|---|---|
| 02:30 | drift: Terraform plan of every stack | infra |
| 03:00 | verify L1–L3, snapshot prune | infra |
| **03:15** | **verify L4 + app config drift for every app/host** | apps |
| 03:30 | OpenClaw nightly scan | openclaw |
| 03:40, 09:40, 15:40, 21:40 | OS patch scan (+ Sev1/2 auto) | infra |
| Mon 04:00 | template refresh check | infra |
| **Mon 04:30** | **app version check** | apps |
| 25th / last Sat 02:00 | OS patch bundle / apply | infra |

### 4.7 Failure ownership

| Fails | Issue | First responder |
|---|---|---|
| infra apply / L1–L3 | `verify: <host>` (infra) | SRE → oc-infra/oc-cfg |
| apps deploy / L4 / app drift | `app-verify: <app>/<host>` (apps) | SRE → oc-cfg |
| dispatch from infra failed | comment on infra PR + `pipeline.failed` | PM → oc-infra |

## 5. Patching (guests and apps)

### 5.1 OS packages — infra, unchanged

Rings (`patch: ring0|1|2` in the spec), 6-hourly scan, Sev1/2 auto with `pre-patch_*` snapshot, Sev3/4 monthly bundle PR (Class C, Faris), never reboots → `patch: reboot <host>` issue.

### 5.2 App versions — apps

| Step | Detail |
|---|---|
| Pin | `app.yaml version.pin` + `sha256` (download verified on the guest, as today) |
| Check | weekly `version-check` (Mon 04:30): upstream latest vs pin → issue `app update: <name> <new>` with release notes link; security releases flagged `Sev2` in the title |
| Bump | oc-cfg opens PR changing `version.*` only. Patch/minor = `bump_class` (B by default); major = C; k3s = C always |
| Roll out | normal app deploy (4.3): snapshot, serial rollout, L4 gate, stg first when the app has stg hosts |
| Urgent | Sev1/2 app release: same PR, PM asks the Reviewer immediately; no auto-merge |

OS packages that an app installs from the distro (e.g. `keepalived` for k3s) are OS packages: patched by 5.1, health-gated by 5.3.

### 5.3 Health gate for OS patching

`patch.yml` (infra) currently has DNS/k3s knowledge baked in. After the split, `patch_apply` runs each host's `app.yaml health` (services + command) after patching that host, from a read-only clone of `homelab-apps` main. Host fails health → rollout stops (same `any_errors_fatal`), SRE gets `pipeline.failed`.

### 5.4 Reboots

Unchanged rule: every reboot waits for Faris (`reboot` comment on `patch: reboot <host>`, openclaw #25). New: the reboot run reads the host's `app.yaml reboot` block:

| policy | Behaviour |
|---|---|
| `one_at_a_time` | reboot, wait for `health`, done |
| `pair` | refuse if the other host of the role in the same zone is unhealthy or also rebooting |
| `drain` | run `before` (drain), reboot, wait for health, run `after` (uncordon); never two control nodes at once |

Host (PVE) reboots stay manual (Faris).

### 5.5 Calendar

| When | OS (infra) | Apps |
|---|---|---|
| every 6 h | scan, Sev1/2 auto | — |
| weekly | — | version check → issues |
| 25th | bundle PR | — |
| last Sat 02:00 | approved bundle | — (app bumps go through PRs any day, inside 08:00–22:00 MYT) |

## 6. `homelab-apps` pipeline and identities

| Piece | Proposal |
|---|---|
| Runner | reuse `host-apply` (same user, same SSH key to guests, same locks). Recommended (Q1) |
| Gitea | repo `homelab/homelab-apps`; branch protection = infra (merge whitelist `ci-merger`, `faris`, `oc-rev`; status check `pr / plan*`; block on rejected reviews; dismiss stale) |
| OpenBao | new AppRole + policy `ci-apps`: create `+/secret/data/apps/*`, read `hm1/platform/ci/apps` (gitea token, svc-apps token); remove `apps/*` from `ci-apply` at cutover |
| Proxmox | new identity `svc-apps@pve` + token, role `DevOpsAppSnapshot` = [VM.Audit, VM.Snapshot, VM.Snapshot.Rollback] on the guest pools only (never protected, never templates). Root applies (`proxmox-acls.yaml`) |
| Gitea dispatch | infra apply uses the existing `gitea_ci_token` to call `workflow_dispatch` on `homelab-apps` (needs write on that repo's actions) |
| Scripts | apps-specific scripts in `homelab-apps/scripts`; shared helpers (`gitea.py`, `ci-lib.sh`, `inventory.py`, `specs.py`, `snapshot.py`) used from the read-only infra clone, pinned to the infra main sha recorded in every report (Q3) |

## 7. Change classes in `homelab-apps` (computed, fail closed)

| Class | Paths / changes |
|---|---|
| C (Faris) | `.gitea/*`, `policy/*`, `scripts/*`, `playbooks/*`, `apps/*/tests/*`, new app, deleted app, `app.yaml` `hosts`/`secrets`/`reboot` changes, version major bump, any `bump_class: C` app (k3s) |
| B (Reviewer) | `apps/*/role/*`, `apps/*/files/*`, `apps/*/vars*`, `app.yaml` `rollout`/`health`/`version` (minor/patch) |
| A (auto) | `apps/*/docs/*`, `README.md`, `docs/*` |

## 8. Secrets

Paths unchanged: `<zone>/secret/apps/<app>/<host>` (create-only at deploy; Faris reads). `store_app_secret` (oc-cfg) unchanged. Only the CI policy that may create them moves from `ci-apply` to `ci-apps`.

## 9. Tests

| Layer | Repo | Runs |
|---|---|---|
| L1 platform, L2 reachability, L3 OS baseline, gone | infra | apply + nightly 03:00 |
| L4 app checks + app config drift | apps | deploy + nightly 03:15 |

Both test folders are Class C. `verify_guest(host)` (PM) re-runs L1–L3; new `verify_app(app, host)` re-runs L4.

## 10. OpenClaw changes (Faris writes `homelab/openclaw`)

| Area | Change |
|---|---|
| `devops_server.py` | `REPOS["apps"]`; write scopes: **oc-cfg** `apps/*/{role,files,vars*,docs,app.yaml}` in homelab-apps (+ unchanged `ansible/**` in infra for guest config); **oc-infra** `.gitea/*`, `scripts/*`, `policy/*`, `playbooks/*` in homelab-apps; remove `apps/*`, `docs/apps/*` from oc-cfg's infra scope after cutover |
| new tools | PM: `verify_app(app, host)`, `redeploy_app(app, hosts)` (dispatch, guarded like `rerun_apply`); all: `app_status(app)` (manifest, pin, last deploy, open app issues) |
| poller | watch `homelab-apps` runs (`deploy`, `verify`, `version-check`) → `pipeline.failed`; Faris comments already cover all repos (add apps) |
| nightly scan, dashboard | add the repo |
| prompts/skills | PM flows (4.2 order rule), `app-method`, `ansible-homelab`, `patching` (5.x), configuration-engineer (owns apps), sre-runbooks (reboot policies, app-verify issues) |

## 11. homelab-standards changes

| File | Change | Owner / class |
|---|---|---|
| `naming/roles.yaml` | optional `app: <name>` per role code (documentation + lint cross-check) | oc-infra / B |
| `openbao/policies/ci-apps.hcl`, `approles.yaml` | new AppRole/policy; later remove `apps/*` from `ci-apply.hcl` | faris / C |
| `proxmox-acls.yaml` | `svc-apps@pve`, role `DevOpsAppSnapshot`, ACLs on guest pools | faris / C |
| `lint/naming.py` | `--repo apps` mode: branch/title rules, folder = app name | oc-infra / C |
| `naming-conventions.md` | app folder naming, `app.yaml`, issue titles `app-verify:`, `app update:` | oc-infra / B |

## 12. Migration plan

Each step is one PR (or one root action), reversible until step 8. **Until step 8 AdGuard and k3s are still deployed by infra; the apps pipeline only runs `--check --diff`.**

| # | Step | Repo / where | Owner | Class | Done when |
|---|---|---|---|---|---|
| 1 | This proposal agreed (all engineers + Faris) | issue | PM | — | 6 verdicts "agree" |
| 2 | Standards: `ci-apps` AppRole/policy, `svc-apps@pve` + role, lint `--repo apps`, conventions | standards | oc-infra + Faris | C | merged |
| 3 | Root: create repo + protection + labels; OpenBao `apply.sh`; Proxmox identity + ACLs; `secret_id` on CT 300 for `runner-apply`; secret `hm1/platform/ci/apps` | Gitea/PVE/OpenBao | Faris (or Claude as root on request) | — | `bao write auth/approle/login` works from CT 300 |
| 4 | Apps skeleton: README, docs (orchestration, patching, adding-an-app), `_example`, manifest schema + check, pr/deploy/verify/version-check workflows **in check mode** | apps | oc-infra (pipeline) + oc-cfg (docs/_example) | C | pr workflow green on a docs PR |
| 5 | Move AdGuard (copy, not delete) | apps | oc-cfg | C (new app) | check-mode deploy on 501/502/601/602 = **0 changed**, L4 green from apps |
| 6 | Move k3s (copy) | apps | oc-cfg | C | check-mode deploy on 310–314 = 0 changed, L4 green |
| 7 | Infra prep: dispatch to apps (flag `APPS_DISPATCH=0`), host locks, patch health from `app.yaml`, reboot policy reader | infra | oc-infra | C | merged, flag off, no behaviour change |
| 8 | **Cutover window** (one evening, Faris present): (a) apps deploy check→apply; (b) infra PR removes app plays/roles/files/`tests/apps`/`docs/apps`, `changed.py` apps rule, sets `APPS_DISPATCH=1`; (c) standards removes `apps/*` from `ci-apply` | apps + infra + standards | oc-infra/oc-cfg, Faris merges | C | full apps deploy = 0 changes; infra apply of a no-op; nightly 03:00 + 03:15 green |
| 9 | OpenClaw tools/scopes/prompts | openclaw | Faris (Claude drafts) | — | agents use `app_status`, `verify_app` |
| 10 | Docs: infra README/docs links, local AIOps docs, runbook rows | all | oc-infra/oc-cfg, Claude | A | — |

Rollback before 8: close the apps PRs; nothing changed. Rollback of 8: revert the infra removal PR (Class C) and switch apps back to check mode; both are single PRs.

## 13. Risks

| Risk | Mitigation |
|---|---|
| Both repos configure the same host during migration | apps check-only until the cutover; cutover is one PR pair in one window |
| Inventory changes under the apps pipeline | it reads infra main at run time and records the sha; dispatch from infra happens after infra main contains the spec |
| New guest green in infra but app broken | apps deploy result is commented on the infra PR; `app-verify` issue opened; stg auto-rollback |
| Lost cross-repo atomicity (guest + app in one PR) | order rule "app first" (an app for a role without hosts is a no-op) |
| Pipeline token sprawl | separate `ci-apps` AppRole and `svc-apps@pve` (snapshot only), no reuse of DevOpsAdmin in apps |
| Deadlock / long waits on host locks | sorted acquisition, 60-min timeout, failure is loud |
| More moving parts for 2 apps | same scripts and gates as infra; most code is reused from the infra clone |

## 14. Open questions (recommendation first)

| Q | Question | Recommendation |
|---|---|---|
| Q1 | Runner for apps | reuse `host-apply` (same SSH key, same locks) |
| Q2 | Pre-deploy snapshots for apps | yes, with new `svc-apps@pve` (snapshot-only) |
| Q3 | Shared scripts | use from read-only infra clone, sha recorded; no copies |
| Q4 | L4 failures | `app-verify: <app>/<host>` issues in homelab-apps |
| Q5 | Version bump class | patch/minor B, major C, k3s always C |
| Q6 | Kubernetes workloads later | decide when the stg cluster exists (`homelab-apps/clusters/` vs separate GitOps repo) |
| Q7 | `base_admin_user` | stays in infra (guest config) |

## 15. Review checklist (each engineer answers on the issue: **agree / agree with changes / object**, with reasons)

| Engineer | Confirm |
|---|---|
| Infrastructure (oc-infra) | boundary 2.2, pipeline/identities 6, classes 7, migration steps 2–4, 7–8 are buildable; dispatch + locks design |
| Configuration (oc-cfg) | app layout 3, `app.yaml` fields cover AdGuard and k3s fully, version/patching 5.2–5.3, check-mode equivalence plan 5–6 |
| Network (oc-net) | no new flows needed (CT 300 → guests SSH as today; upstream GitHub for version check via existing egress); zones untouched |
| Reviewer (oc-rev) | classes 7 fail closed; nothing lets an app PR reach Terraform, protected guests or secrets policy; evidence in PR comments is enough to review |
| SRE | failure ownership 4.7, nightly timeline 4.6, reboot policies 5.4, rollback of step 8, runbooks to write |
| PM | flows 4.2–4.5 are routable; tool changes 10; nothing requires agents to exceed their scopes |
