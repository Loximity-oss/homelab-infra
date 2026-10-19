# 5. Pipeline, classes, OpenClaw and standards

## 5.1 `homelab-apps` pipeline and identities

| Piece | Proposal |
|---|---|
| Workflows | `pr` (lint, render, check-mode diff, classify, gate), `deploy` (push to main + `workflow_dispatch` from infra), `verify` (03:15 + dispatch), `version-check` (Mon 04:30) |
| Runner | reuse `host-apply` (same user, SSH key to guests, locks on CT 300) — Q1 |
| Gitea | repo `homelab/homelab-apps`; branch protection = infra (merge whitelist `ci-merger`, `faris`, `oc-rev`; required check `pr / plan*`; block on rejected reviews; dismiss stale approvals); labels `class/A,B,C`, `wip`, `app-verify`, `app-update`, `build` |
| Reporting | the `ci` bot token (stored at `hm1/platform/ci/apps`) has comment rights on both repos, so a deploy dispatched by infra comments on the infra PR |
| OpenBao | new AppRole + policy `ci-apps`: create `+/secret/data/apps/*`; read `secret/data/hm1/platform/ci/apps`. At cutover `apps/*` leaves `ci-apply` |
| Proxmox | new `svc-apps@pve` + token, role `DevOpsAppSnapshot` = [VM.Audit, VM.Snapshot, VM.Snapshot.Rollback] on the guest pools only — never protected VMIDs, never templates. Root applies (`proxmox-acls.yaml`) — Q2 |
| Shared code | `gitea.py`, `ci-lib.sh`, `inventory.py`, `specs.py`, `snapshot.py` used from the read-only infra clone (sha in every report); apps-only scripts in `homelab-apps/scripts` — Q3 |

## 5.2 Change classes in `homelab-apps` (computed, fail closed)

**Default for any path not listed = C.** Globs are matched per path segment (no `*` across `/`). Field-level
judgement compares base and head YAML leaves, like `template_change()` in infra.

| Class | Change |
|---|---|
| **C** (Faris) | `.gitea/*`, `policy/*`, `scripts/*`, `playbooks/*`, `catalog/*/tests/*`; new or deleted catalogue entry; entry `catalog.role`, `catalog.secrets`, `catalog.flows`, `reboot`, `probes` changes; status → `stable`; new or deleted deployment; deployment `hosts` or `zone` changes; version **major** bump; any bump of an entry with `bump_class: C` (k3s) |
| **B** (Reviewer) | `catalog/*/role/*`, `catalog/*/files/*`; entry `rollout`, `health`, `version` (patch/minor); deployment `vars`, `host_vars`, `version` pin, `wave` (≥ 2) |
| **A** (auto) | `catalog/*/docs/*`, `docs/*`, `README.md` |

A version change is judged by semantic version (major/minor/patch); an unparsable version = C.

## 5.3 OpenClaw changes (`homelab/openclaw`, Faris writes)

| Area | Change |
|---|---|
| `devops_server.py` repos | `REPOS["apps"] = "homelab/homelab-apps"` |
| write scopes | **oc-cfg**: `catalog/*/{role,files,tests,docs}/**`, `catalog/*/app.yaml`, `deployments/**` (tests still Class C); keeps `ansible/**` (not keys) in infra for guest config. **oc-infra**: `.gitea/*`, `scripts/*`, `policy/*`, `playbooks/*`, `docs/*` in apps. After cutover oc-cfg loses `apps/*`, `docs/apps/*` in infra |
| new tools | all roles: `list_catalog()`, `app_status(app)` (entry, pin, deployments, last deploy, open issues). PM: `plan_from_catalog(app, zone, count=0)` (read-only, deterministic), `verify_app(app, host)` (dispatch apps `verify`), `redeploy_app(app, zone)` (dispatch `deploy`, guarded like `rerun_apply`) |
| poller | `homelab-apps` runs (`deploy`, `verify`, `version-check`) → `pipeline.failed`; Faris comments on apps issues/PRs (already generic) |
| nightly scan, dashboard | add the repo; dashboard shows deployments and wave state |
| monitoring | blackbox exporter, `file_sd` sync job, probe alerts (4.3) |
| prompts / skills | PM: flows 2.2–2.4 and the build issue; `app-method` → catalogue method; `ansible-homelab`; `patching` (3.x); configuration-engineer owns the catalogue; sre-runbooks: reboot policies, `app-verify` issues, probe alerts |

## 5.4 homelab-standards changes

| File | Change | Owner / class |
|---|---|---|
| `naming/roles.yaml` | optional `app: <catalogue entry>` per role code | oc-infra / B |
| `openbao/policies/ci-apps.hcl`, `openbao/approles.yaml` | new AppRole/policy; later remove `apps/*` from `ci-apply.hcl` | faris / C |
| `proxmox-acls.yaml` | `svc-apps@pve`, role `DevOpsAppSnapshot`, ACLs on guest pools | faris / C |
| `flows.yaml` | mgt → wifi/iot :53/:80, mgt → shr :6443 (probes) | oc-net / C |
| `lint/naming.py` | `--repo apps`: branch/title rules, entry folder = name, deployment path = zone/app | oc-infra / C |
| `naming-conventions.md` | catalogue/deployment naming; issue titles `app-verify:`, `app update:`, `build:`; snapshot `pre-deploy_<ts>` | oc-infra / B |

## 5.5 Secrets

Paths unchanged: `<zone>/secret/apps/<app>/<host>` (create-only at deploy; Faris reads). `store_app_secret` (oc-cfg)
unchanged. Only the CI policy that creates them moves (`ci-apply` → `ci-apps`).
