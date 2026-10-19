# 5. Pipeline, identities, classes, OpenClaw and standards

## 5.1 Pipeline and identities

| Piece | Decision (rev 3) |
|---|---|
| Workflows | `pr` (`pull_request_target`: main's workflow/scripts/policy on PR data, **offline only**), `deploy` (push to main + dispatch, main ref only), `verify` (03:15 + dispatch), `version-check` (Mon 04:30) |
| PR runner | `host-plan` (user `runner`, no guest SSH key, no OpenBao create rights) |
| Deploy/verify runner | **new label `host-apps`, Unix user `runner-apps`** on CT 300 (Q1 decided by the reviewer's finding): own SSH key `ci-apps` (public key in infra `ansible/keys/ci-apps.pub`, Class C; `base_common` installs it **only on hosts listed in a deployment**, read from the apps clone via `gitea_ci_token`; if that clone fails, existing `ci-apps` keys are **left unchanged** (never removed) and the apply warns and upserts `pipeline: apps clone failed` (reviewer 3.2-g, infra 3.3-1), own OpenBao AppRole files `/etc/openbao/ci-apps/{role_id,secret_id}`; `runner-apply` and `runner-apps` cannot read each other's files; only `/var/lib/devops/locks` is shared via group `devops-locks` |
| OpenBao | AppRole `ci-apps` (client `runner-apps`, 192.168.70.20/32), policy `ci-apps`: read `secret/data/hm1/platform/ci/apps` (`pve_token_apps`, `gitea_apps_token`) and `auth/token/create/apps-*` only. **One policy per app** `ci-apps-<app>`: `create` only (never `update`, so create-only is enforced server-side), on explicitly listed guest-zone mounts, both `<zone>/secret/data/apps/<app>` and `<zone>/secret/data/apps/<app>/*` (reviewer 3.2-c) and a token role `apps-<app>` (`allowed_policies: [ci-apps-<app>]`, TTL 30 min, non-renewable). `deploy.yml` mints one child token per app play; a role can only ever create its own app's secrets (reviewer 15). Test in step 5: the adguard token is denied on `apps/k3s/*`. New app = its policy + token role in the same Faris standards PR. Apps scripts call `bao_login ci-apps` |
| Secret writes | **`bao_create_secret`**, a pipeline-owned role in `playbooks/roles/` (Class C, written once; reviewer 16, cfg B1): runs on CT 300 (the AppRole is bound to 192.168.70.20); inputs `path`, `keys` and either `data` or `generate: {length, chars}` plus optional `hash: bcrypt`; validates the path against the app's `catalog.secrets` and **zone/host it looks up itself from the infra specs by `inventory_hostname`** (never from variables a caller can pass; test: `include_role` with `vars: {deploy_zone: int}` cannot change the path) (reviewer 3.2-a, 3.3); reads `BAO_ZONE_MODE` and the per-app child token itself; writes create-only (`cas=0`; an existing secret = "already created", not an error); returns **only the hash** (plaintext never leaves the role); `no_log` inside; does nothing in check mode. Marker and hash files stay on the guest as today. `app_adguard`'s password block and `svc_k3s` escrow move to it (steps 7–8) (cfg 3.2 clarification) |
| Proxmox | `svc-apps@pve` + token, role `DevOpsAppSnapshot` = [VM.Audit, VM.Snapshot] on the pools of zones that have deployments (wifi, iot, shr today; never dmz, so VM 210 is unreachable), plus `VM.Snapshot.Rollback` on the **stg pool only** (reviewer 17). The apps side of `snapshot.py` creates and deletes only `pre-deploy_*` names (a client-side limit: `VM.Snapshot` could delete any snapshot on those pools — **accepted risk**, reviewer 3.2-d). Nightly scan asserts templates and protected VMIDs are in no guest pool |
| Gitea | repo `homelab/homelab-apps`; protection = infra (merge whitelist `ci-merger` (A, class recomputed by main's policy), `oc-rev` (B), `faris`; required `pr / plan*`; block on rejected; dismiss stale) **plus "restrict approvals to whitelist"** (`oc-rev`, `faris`) so approvals by `ci-apps`, `oc-cfg`, `oc-infra` never count (reviewer 3.2-e); bot `ci-apps` distinct from `ci-merger` |
| Shared code | from the read-only infra clone: `gitea.py`, `ci-lib.sh` (`lock_hosts`, `bao_login`), `inventory.py`, `specs.py`, `snapshot.py`; infra sha in every report |

**PR lint gate, ansible-core pin and privilege separation:** see `5b-security.md`.

Deployment `vars`/`host_vars`: banned keys and no Jinja (1.7, reviewer 13); `host_vars` keys must be hosts of that
deployment.

**Deploy guard (`playbooks/deploy.yml` `pre_tasks`, defence in depth):** host is in infra specs, VMID not in
`protected.yaml`, `ansible_host` = spec IP, zone = deployment zone. Any mismatch fails the play before the role.

## 5.2 Change classes (computed by `policy/classify.py`, fail closed)

**Rules:** a changed path or leaf not listed as A or B is **C**. Paths use a real segment matcher (`*` = one segment,
`**` = any depth), never `fnmatch`. YAML is judged leaf by leaf (base vs head, like `template_change()`); missing
base/head checkout, unparsable YAML or a non-mapping → C. Renames are judged on old **and** new path. Symlinks
(120000) and submodules (160000) → C. Self-referential fields (`bump_class`) use the **stricter** of base and head.
Unit tests cover every row below with real paths (`catalog/adguard/role/tasks/main.yml`, `.../files/sub/x.j2`).

| Class | Paths and leaves |
|---|---|
| **A** (auto, no deploy) | `catalog/*/docs/**`, `docs/**`, `README.md` |
| **B** (Reviewer) | `catalog/*/role/{tasks,defaults,handlers,templates}/**` (the matcher supports `{a,b}`), `catalog/*/files/**`; `app.yaml` leaves `catalog.summary`, `owner`, `catalog.size`, `catalog.os`, `catalog.ports`, `rollout.serial`, `rollout.order`, `version.pin` + new `version.known` entry for a **patch/minor** bump; deployment `vars`, `host_vars`, `version` (pin from `known`), `pin_until`, `wave` (≥ 2) |
| **C** (Faris) | everything else, explicitly incl.: `.gitea/**`, `policy/**`, `scripts/**`, `playbooks/**`, `ansible.cfg`, `requirements.yml`, `.gitleaks.toml`, `.yamllint`, `.ansible-lint`; `catalog/*/tests/**`; role `meta/**` (`dependencies`, `collections` pull in outside code; reviewer 19), `library/`, `action_plugins/`, `filter_plugins/`, `lookup_plugins/`, `module_utils/`, `vars/`; `environments.yaml`; new/deleted entry or deployment; `name`, `catalog.role`, `catalog.pattern`, `catalog.status` (any), `catalog.flows`, `catalog.secrets`, `health` (all of it), `rollout.stop_on_failure`, `rollout.health_between_hosts`, `rollout.groups` (hooks run commands), `reboot`, `probes`, `peer_check`, `drift`, `tests`, `version.source`, `version.bump_class`, a `version.known` sha256 changed for an existing version, major bumps, any bump of a `bump_class: C` entry (k3s); deployment `app`, `zone`, `hosts`, `pattern`, `canary_waiver` |

A docs-only (A) merge does not trigger `deploy`. For a B bump, the PR job fetches the upstream checksum file and
fails if the new `version.known` sha256 differs (reviewer note). Version judgement: semantic version of the part before `+`; build
metadata change = patch; unparsable = C.

## 5.3 OpenClaw changes (`homelab/openclaw`, Faris writes; **before** any apps PR by agents)

| Area | Change |
|---|---|
| repos | `REPOS["apps"]`; every tool taking `repo` accepts `apps` (`read_file`, `list_files`, `get_pr_status`, `list_open_prs`, `list_issues`, `read_issue`, `comment_issue`, `close_issue`, `review_pr`, writer tools) |
| write scopes | **oc-cfg**: `catalog/**` (tests still C), `deployments/**`, `playbooks/**` (except `playbooks/roles/bao_create_secret/**`). **oc-infra**: `scripts/**`, `policy/**`, `docs/**`, `README.md`. **Nobody (agents): `.gitea/**`** — workflows are written by Claude/Faris (reviewer 18c). After cutover oc-cfg loses infra `apps/**`, `docs/apps/**`. `oc-pm` has no Gitea write on apps; its dispatch tools use the server-side `ci-apps` token |
| new tools | all: `list_catalog()`, `app_status(app)` (entry, pin, deployments, waves, last deploy, open issues), `app_verify_status(app, host)`. PM: `plan_from_catalog(app, zone, count=0, mode="build"\|"remove", create_issue=True)`, `verify_app(app, host)`, `redeploy_app(app, zone, from_wave=0)`, `reboot_guest(issue)` (silence + dispatch infra `reboot`) |
| fixes found in review | `comment_issue` truncates at 6 000 chars (raise to 15 000 and split longer bodies); `read_file` cap 20 000 (documented) |
| poller | apps runs (`deploy`, `verify`, `version-check`) → `pipeline.failed`; Faris comments (only `faris`; `ci-apps` comments are never Faris events or reviews) |
| `get_pr_status` on apps (reviewer 22) | class + all reasons, lint-gate result, per-host offline render diff; after merge the deploy/L4 results in `tests` |
| nightly scan, dashboard | add the repo; pool check (5.1); dashboard: deployments, waves, pins |
| monitoring | blackbox exporter, `file_sd` job, alerts, inhibit rules (4.3) |
| prompts / skills | PM reboot rule: Faris's `reboot` comment → `reboot_guest(issue)`, never a direct SRE reboot (updated **before** cutover, PM 3.3); PM flows 2.2–2.5 and tracking issues (`oc-pm` has read on apps, which is enough to open issues there); `app-method` → catalogue method; `ansible-homelab`; `patching` (3.x); `review-checklist`: apps section; configuration-engineer owns the catalogue; sre-runbooks (below) |
| runbooks (before cutover) | `app-verify` triage; probe alerts by `app`/`zone`/`host`; manual rollback from `pre-deploy_*`; k3s set restore after `etcd-snapshot save`; reboot workflow failures; stale locks |

## 5.4 homelab-standards changes

| File | Change | Owner / class |
|---|---|---|
| `naming/roles.yaml` | optional `app:` per role code | oc-infra / B |
| `naming/secrets.yaml` | `hm1/platform/ci/apps` (`pve_token_apps`); `hm1/platform/ci/apply` gains `gitea_apps_dispatch_token` | faris / C |
| `openbao/policies/ci-apps.hcl`, `ci-apps-<app>.hcl`, token roles `apps-<app>`, `openbao/approles.yaml` | `ci-apps` (client `runner-apps`) + one policy and token role per app; later remove app lines from `ci-apply.hcl` | faris / C |
| `proxmox-acls.yaml` | `svc-apps@pve`, `DevOpsAppSnapshot` on deployment-zone pools, Rollback on stg pool only | faris / C |
| `flows.yaml` | machine-readable `flows:` schema (C, prerequisite); entries: CT 100 → shr :6443, CT 100 → wifi/iot DNS IPs (+ three UDM policies, Faris); fix the stale note (wifi SSH is implemented) and reconcile mgt (/29 vs live /28, DHCP) (net R3) | oc-net / C then B |
| `lint/naming.py` | `--repo apps`: branch/title rules, entry folder = name, deployment path = zone/app | oc-infra / C |
| `naming-conventions.md` | catalogue/deployment naming; issues `app-verify:`, `app update:`, `build:`, `remove:`; snapshots `pre-deploy_`, `pre-reboot_` | oc-infra / B |

## 5.5 Secrets

Paths unchanged (`<zone>/secret/apps/<app>[/<host>]`); keys per entry (1.5). Created by the role at deploy
(create-only, `ci-apps`) or supplied via `store_app_secret` (oc-cfg). Faris reads them.
