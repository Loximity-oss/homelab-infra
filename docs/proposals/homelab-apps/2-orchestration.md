# 2. Orchestration

## 2.1 Who triggers whom

```
homelab-infra  PR merged → apply: locks → snapshot → Terraform → known_hosts → Ansible base → L1–L3 (→ stg rollback, re-test)
                 → hosts with no failed row in verify.json AND in a deployment → workflow_dispatch apps deploy(hosts) → release locks
homelab-apps   deploy: locks (waits for infra to release) → snapshot pre-deploy_<ts> → role per deployment → L4 → report
                 → comment on the infra PR + own run log
homelab-apps   PR merged → deploy(changed entries/deployments) in waves
```

- **Triggers are one-way** (infra → apps). Reads go both ways, read-only (1.3).
- **Dispatch point:** `ci-apply.sh`, after the tests block (incl. stg rollback re-test), before `finish`, locks still
  held. Hosts = `VHOSTS` ∩ rows of **`$W/verify/verify.json`** (not the `$OUT` copy) with no `fail`, minus rolled-back
  hosts (infra R3-4). If the Ansible run failed (`ok ≠ 0`), nothing is dispatched. Membership: `ci-apply.sh` clones
  apps main with the infra `gitea_ci_token` (read on homelab-apps, step 4) and keeps hosts that appear in a
  deployment; a failed clone = no dispatch + ⚠️ warning, never an apply failure (infra R3-2).
- **Switch:** `APPS_DISPATCH` env in `.gitea/workflows/apply.yml` (`0` until cutover step 10(d)).
- **API:** `POST /repos/homelab/homelab-apps/actions/workflows/deploy.yml/dispatches` (ref `main`) with a token of a
  new Gitea bot **`ci-apps`**. Gitea tokens cannot be dispatch-only (`write:repository` also allows pushes), so the
  real limits are (reviewer 18, infra R3-1): write on homelab-apps only; **branch protection rule `*` on homelab-apps
  allows pushes only for `oc-cfg`, `oc-infra`, `faris`** (so `ci-apps` cannot create a branch); protected `main`;
  `deploy`/`verify` refuse any ref but `main`; on infra only read + comment; never in any merge whitelist; its comments
  are never reviews and never Faris events (poller trusts only `faris`). Separate tokens of this bot: infra apply
  (`hm1/platform/ci/apply` key `gitea_apps_dispatch_token`), the apps pipeline (`hm1/platform/ci/apps`), and the
  OpenClaw PM tools (aiops secrets, server-side; agents never see it). **`oc-pm` has no Gitea write on apps.**
- **Untrusted dispatch:** apps `deploy`/`verify` refuse any ref other than `main` and check out main's sha; inputs
  (`hosts`, `infra_pr`) are intersected with deployments ∩ specs. Write on apps: `ci-apps` (push-blocked), `faris`,
  and the PR authors `oc-cfg`/`oc-infra`. **No agent can write `.gitea/**`** (removed from oc-infra's apps scope;
  workflows are Faris/Claude-written, Class C), so a pushed branch only carries main's workflow definitions, whose
  `deploy` runs on `main` only and whose `pr` uses `pull_request_target` (main's code): branch pushes never run
  unreviewed workflow code (closes reviewer 18c).
- **Dispatch failure** never fails the infra apply: the apply comments ⚠️ and upserts issue
  `pipeline: apps dispatch failed` (label `pipeline`) → PM → oc-infra.
- **Inventory:** apps clones infra main read-only (`inventory.py`, `specs.py`); the infra sha is in every report.

## 2.2 Flows

The PM tracks every multi-PR change on **one issue** with a checklist of its PRs in dependency order; each PR stays
WIP until its predecessors are merged. Every flow ends with the SRE verifying (`verify_guest` + `verify_app`) before
the PM reports and closes.

### A. New host for an existing deployment
| # | Step | Repo | Who | Gate |
|---|---|---|---|---|
| 1 | Flows (gateway-routed zones only, 2.4 steps 4/4b) | standards + UniFi | oc-net, Faris | B / C |
| 2 | Add host to `deployments/<zone>/<app>.yaml` | apps | oc-cfg | C |
| 3 | Guest spec (IP = planned IP) | infra | oc-infra | A (B in shr) |
| 4 | Apply → L1–L3 → dispatch → deploy → L4 | pipeline | — | — |
| 5 | Verify, report, close | SRE, PM | — | — |

Either order of 2 and 3 works: a deployment host missing from specs is a warning and is skipped; merging the
deployment deploys to hosts that already exist; a new guest is deployed by the infra dispatch once it is green.

### B. App change (role, files, config, version)
PR → `pr` workflow on `host-plan` (no guest SSH key): yamllint, ansible-lint, `catalog.py check`, gitleaks,
**offline render** of every template for every target host (before/after diff, secrets as placeholders), classify,
gate. No PR code ever runs against a guest. Merge → `deploy` in waves (2.5) → L4 per wave → PR comment.

### C. Guest change (OS baseline, size, template)
Infra only. A replace dispatches the deploy for that host (2.1). In-place base changes do not redeploy apps; nightly
L4 catches side effects.

### D. Remove an app from a zone / destroy its guests
Order: (1) deployment removed or host taken out (apps, C) **first**, while the host still exists; (2) guest destroy
(infra, C). `plan_from_catalog(app, zone, mode="remove")` produces this plan; the PM opens the tracking issue
`remove: <app> <zone>`. A role-code change = rebuild (replace).

## 2.3 Locks

| Rule | Detail |
|---|---|
| Helper | `lock_hosts <csv>` in infra `ci-lib.sh` (apps uses it from the infra clone): sorted, one fd per host, `flock -w 3600`, released on exit |
| Files | `/var/lib/devops/locks/host-<hostname>.lock`. Directory `root:devops-locks` mode **3770** (setgid + sticky, not world-writable): no runner can unlink or rename another's lock file (infra F, reviewer 21). Step 4 pre-creates files for all current hosts (`0664`, group `devops-locks`); new ones are created with `umask 002`. `lock_hosts` opens with `exec {fd}>>file` (append) and never deletes or recreates a file |
| infra apply | locks taken right after `plan_guests.py` + `HOSTS` are computed, **before the pre-apply snapshot**; set = create ∪ replace ∪ update ∪ delete ∪ `HOSTS`; whole fleet when `ansible_all` or `tests/` changes |
| patching | `scan` takes none (read-only); `patch_run()` locks its whole host set in **one** `lock_hosts` call before `snapshot.py take` (infra 3.3-2) |
| verify | `ci-verify.sh` locks its whole host set (`--all` = fleet) in one call, for the whole run; `finish()`/exit releases all locks |
| apps deploy / verify | lock the deployment hosts for the whole run |
| reboot | new infra `reboot` workflow (3.4) locks the host for the whole reboot; agents never reboot patched hosts directly |
| Owner files | `lock_hosts` writes `run`, `repo`, `workflow`, `pid`, `start` to a sidecar `host-<hostname>.owner` (same `3770` directory); a lock-wait timeout issue names the holder and notes it may be stale (flock releases on process death) (SRE 3.7-2) |
| Timeouts | job `timeout-minutes` in `apply.yml`, `patch.yml`, `verify.yml`, `reboot.yml`, apps `deploy.yml` = 240 (> 60 min lock wait + longest run). A lock-wait timeout upserts issue `pipeline: lock wait timeout <host>` (SRE R3-5) |
| Templates | existing `templates.lock` unchanged |

## 2.4 Build from the catalogue ("PM, build Uptime Kuma in int")

| # | Step | Who | Gate |
|---|---|---|---|
| 1 | `plan_from_catalog(app, zone, count=0, create_issue=True)`: hostnames, VMIDs, IPs, spec fields from `size`/`os` (datastore left to oc-infra), pattern → guest count, flows resolved per router (1.8), secrets, PR list with owner/repo/class/order, one brief per step `{goal, context, constraints, done_when}`, and a **RAM check counting live and planned guests** | PM | — |
| 2 | Refusals as reasons: `beta` outside stg, `deprecated`, zone without guests, RAM short, role code missing | tool | — |
| 3 | The tool opens issue `build: <app> <zone>` with the plan and PR checklist | tool | — |
| 4 | Flows: coresw zones → `flows.yaml` entry only (B, non-blocking); gateway zones → `flows.yaml` (B) **+ UDM policy proposal (C, Faris creates the policy)**; none needed → explicit "no new flows" | oc-net | B / C |
| 4b | Gateway zones only: oc-net reads UniFi, confirms the policy, sets `state: implemented`; steps 6–7 wait for it | oc-net | — |
| 5 | `deployments/<zone>/<app>.yaml` | oc-cfg | C |
| 6 | Guest spec(s); IP must equal the planned IP (checked) | oc-infra | A (B in shr) |
| 7 | Apply → L1–L3 → dispatch → deploy → L4; probes generated | pipeline | — |
| 8 | SRE verifies (`verify_guest`, `verify_app`) | SRE | — |
| 9 | PM reports endpoints, OpenBao path of the admin secret, tests; closes the issue | PM | — |

Faris: approves the Class C PRs; in wifi/iot also creates the UDM policy.

**Adding an app to the catalogue:** oc-cfg writes the entry (`beta`) + a stg deployment; team review; Faris approves
(C). `beta` → `stable` (C) after 7 days green in stg.

## 2.5 Waves

- stg deployments = wave 1 (enforced), others ≥ 2. Wave N+1 starts only when every host of wave N passed L4 **and**
  `wave_delay` has passed (default 1 h; probes must be green over it).
- Hosts inside a deployment go one at a time, the next only after the previous passes `health`.
- **Wave failure:** the run stops, raises `pipeline.failed` and an `app-verify: <app>/<host>` issue; remaining waves
  stay pending (shown by `app_status`). Changed hosts: stg rolls back automatically; elsewhere the SRE restores from
  `pre-deploy_*` and the PM opens a revert PR. `redeploy_app(app, from_wave=N)` resumes.
- No stg deployment (wave 1 empty): wave 2 starts at once, no `wave_delay` (reviewer note). A `stable` entry without
  a stg deployment is a **warning** while Q10/Q11 are deferred (1.6); `canary_waiver` applies only after that.
- Waves come from `environments.yaml` (1.9): stg 1, shr/int/wifi 2, iot 3, dmz 3 — so wifi and iot DNS never change in
  the same wave (cfg R3).
- An infra-dispatched deploy of one new/replaced host runs outside wave order; that is correct because the catalogue
  version is unchanged (infra R3-5).
- `redeploy_app(app, from_wave=N)` refuses unless every wave below N is green on the same apps sha (reviewer 20).

## 2.6 Daily timeline (MYT)

| Time | Job | Repo |
|---|---|---|
| continuous | probes every 60 s → Alertmanager → OpenClaw incident | openclaw |
| 02:30 | Terraform drift | infra |
| 03:00 | verify L1–L3, snapshot prune (`pre-apply_`, `pre-patch_`, `pre-deploy_`, `pre-reboot_`) | infra |
| 03:15 | verify L4 + app drift | apps |
| 03:30 | OpenClaw nightly scan | openclaw |
| 03:40 / 09:40 / 15:40 / 21:40 | OS patch scan (+ Sev1/2 auto) | infra |
| Mon 04:00 / 04:30 | template refresh check / app version check | infra / apps |
| 25th / last Sat 02:00 | OS patch bundle / apply | infra |

## 2.7 Failure ownership

| Fails | Signal | First responder → owner |
|---|---|---|
| infra apply / L1–L3 | `verify: <host>`, PR comment | SRE → oc-infra / oc-cfg |
| apps deploy / L4 / drift / wave stop | `app-verify: <app>/<host>`, PR comment | SRE → oc-cfg |
| probe alert | Alertmanager → incident run | SRE |
| apps dispatch | ⚠️ comment + `pipeline: apps dispatch failed` | PM → oc-infra |
| lock wait timeout | `pipeline: lock wait timeout <host>` (names the holder) | SRE → oc-infra |
| PR lint / render / `bwrap` failure | red `pr / plan` check on the PR, **not** an issue | PR author |
| version release / pin expired | `app update: <app> <version>` | PM → oc-cfg |

All of these are resolved and closed by the agents with evidence; only Class C, root and openclaw work go to Faris.
