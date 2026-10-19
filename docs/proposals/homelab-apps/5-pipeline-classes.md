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

**PR lint gate** over **everything under `catalog/<app>/`** — role tasks, handlers, defaults, vars, every `.j2`
anywhere in the entry (incl. `files/**`), and any file used as a `template` src — before render. A lint failure turns
the `pr / plan` check **red**, so the PR cannot merge and the render diff is skipped (reviewer 12, 3.2 notes 1, f):
- **Jinja is checked on the parsed AST** (`jinja2.Environment().parse()` of every expression and template), not on
  raw text, because names can be built at render time (reviewer 3.2-2, 3.3):
  - the `attr` filter is rejected;
  - an `attribute=` argument (on `map`, `selectattr`, `rejectattr`, `sort`, `groupby`, `sum`, `min`, `max`,
    `unique`, …) must be a constant string that does not start with `_`;
  - `Getattr` names starting with `_` or equal to `mro` are rejected;
  - `Getitem` subscripts must be constants not starting with `_` and not `mro`; the only variable subscript allowed is
    `hostvars[<var>]` (HostVars returns Undefined, never falls back to attributes). `groups[...]` takes a constant only
    (`role_<code>`), because a dict `getitem` falls back to `getattr` (reviewer 3.5-1);
  - **filters and tests are allow-listed** (`policy/jinja-allow.yaml`, Class C); anything else fails. Filter names
    passed as strings (`map('attr', …)`, `select`/`reject`/`selectattr`/`rejectattr` with a non-allow-listed name) and
    `extract` with a non-constant or `_`-leading argument are rejected (reviewer 3.5-2);
  - every expression context is parsed, not only `{{ }}`: `when`, `changed_when`, `failed_when`, `until`,
    `assert.that`, `loop`, `with_*`, and every templated module argument (reviewer 3.5-5);
  - every YAML scalar under `catalog/<app>/role/**` (defaults, vars, task args) containing `__` or equal to `mro` is
    rejected, as for deployment values (reviewer 3.5-4);
  - any constant string containing `__` or equal to `mro` is rejected (catches `'\x5f\x5f'`, `'_'*2` results stay
    non-constant and fall under the rules above);
  - unit tests, one per bypass: `attr('_'~'_init_'~'_')`, `'_'*2`, `'%c%c'|format(95,95)`, `'\x5f\x5f'`,
    `map(attribute=x)`, `foo[var]`, a deployment var holding `__globals__`;
- rejects `local_action`, `connection: local`, `add_host`, `fetch`, `synchronize`, `script`, `raw` on localhost;
- `delegate_to` only to **another host of the same deployment**: a host name, an expression over
  `groups['role_<code>']`, or a **bare variable** (e.g. `svc_k3s_init_host`) — a variable is accepted because the
  deploy guard checks every resolved target at run time (same deployment, not protected); never `localhost`,
  `127.0.0.1` or the controller; unit test (cfg 3.2-B1, 3.5-1);
- `check_mode: false` only on tasks that also have `changed_when: false` and use a read-only module (`command`,
  `shell`, `slurp`, `stat`, `uri` with GET) (cfg 3.2-B2);
- the banned names (`zone`, `vmid`, `role`, `type`, `os`, `bao_*`, `deploy_*`) may not be set by `set_fact`/`register`, any task/block/`include_role` `vars:`, role params, or an `include_vars` file (reviewer 3.2-a, 3.3);
- rejects every lookup and query in any spelling — `lookup(...)`, `query(...)`, `q(...)`, FQCN
  (`ansible.builtin.*`, `community.*`) and `with_<lookup>` loops — except the plain loop forms `with_items`,
  `with_dict`, `with_list`, `loop`;
- `src:` of `copy`/`template`/`unarchive`/`assemble`, `file:`/free-form of `include_vars`, and the target of
  `include_tasks`/`import_tasks` must **resolve statically** under `catalog/<app>/` (absolute paths, escapes and
  unresolvable templated paths rejected; `{{ role_path }}/../files` allowed); `remote_src: true` exempt (cfg R3,
  reviewer 3.2-b);
- any `ansible_*` key in role `vars/`/`defaults/`;
- **no exceptions and no per-role allow-lists**: the only controller-side action a role may use is `include_role:
  bao_create_secret`.

**ansible-core ≥ 2.19** (no re-templating of strings built at runtime) pinned in `requirements.yml` and on both
runners; `deploy.yml` `pre_tasks` assert the version (reviewer 3.5-3).

**Privilege separation — the lint is defence in depth, not the only boundary** (Claude, rev 3.6; answers the class of
findings in reviewer 3.2-2, 3.3, 3.5):
- **Deploy:** `ci-deploy.sh` (main's code, as `runner-apps`) logs in to OpenBao, mints the per-app child tokens, takes
  the `pre-deploy_*` snapshots and fetches inventory **first**, then runs `ansible-playbook` via `sudo -u
  runner-apps-play` (a separate unprivileged user, sudoers rule for that one command). `runner-apps-play` can read
  **none** of: `/etc/openbao/*`, the Proxmox token, the Gitea tokens, `$PLANS`. It receives only the run's child tokens
  (env, 30-min TTL, create-only on its own app) and read access to the `ci-apps` SSH key. Code that escapes on the
  controller gains nothing beyond what the role already has on its own guests.
- **PR render (host-plan):** the offline render runs inside `bwrap` with no network, a read-only bind of the PR tree
  and the infra/apps clones, an empty `/etc/openbao`, and no access to the runner's home or `$PLANS`. An escape during
  render reaches nothing.
- Unit/integration tests in step 5 prove both: a role that tries to read `/etc/openbao/ci-apps/secret_id` or the
  Proxmox token fails with permission denied at deploy, and a render that tries `curl` fails with no network.

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
