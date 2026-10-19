# 2. Orchestration

## 2.1 Who triggers whom (one direction only)

```
homelab-infra  PR merged → apply: Terraform → known_hosts → Ansible base (site.yml) → L1–L3
                                                        │ hosts that passed L1–L3 AND are in a deployment
                                                        ▼ workflow_dispatch(hosts, reason, infra_pr)
homelab-apps   deploy: lock → snapshot pre-deploy_<ts> → role per deployment (rollout rules) → L4 → report
                                                        └→ comment on the infra PR (ci bot) + own run log

homelab-apps   PR merged → deploy(changed entries/deployments) in waves: wave 1 (stg) → L4 green → wave 2 …
```

- **Insertion point in infra:** `scripts/ci-apply.sh`, after the "Tests (decision 32)" block and before `finish`,
  only for hosts whose L1–L3 rows passed and that appear in a deployment (read from a `homelab-apps` main clone).
  Behind `APPS_DISPATCH=0|1` (off until cutover).
- **Dispatch API:** `POST /repos/homelab/homelab-apps/actions/workflows/deploy.yml/dispatches`, the same call
  `verify_guest` and `build_template` already use on homelab-infra.
- **Apps never triggers infra.** Inventory: the apps pipeline clones `homelab-infra` main read-only and uses
  `scripts/inventory.py` + `specs.py`; the infra sha is recorded in every report.

## 2.2 Flows

### A. New guest for an existing deployment (e.g. a third resolver)
| # | Step | Repo | Who | Gate |
|---|---|---|---|---|
| 1 | Guest spec, `role: dns` | infra | oc-infra | A (B in shr) |
| 2 | Add the host to `deployments/<zone>/adguard.yaml` | apps | oc-cfg | **C** (widens targets) |
| 3 | Apply creates + L1–L3 → dispatch → deploy → L4 → comment | both | pipeline | — |

Order rule: **deployment PR may merge first** — a host that does not exist yet is skipped with a warning; the infra
dispatch deploys it as soon as it is green. (Merging the guest first is also safe: nothing deploys until step 2.)

### B. App change (role, files, config, version)
App PR → pr workflow: lint (yamllint, ansible-lint, catalog check, gitleaks) → render templates for every target host
(offline) → `ansible-playbook --check --diff` on the live targets (read-only path, no secret creation) → classify →
gate → merge → deploy in waves → L4 per wave → PR comment. stg deployments roll back automatically on failed L4
(snapshot), same rule as infra D3.

### C. Guest change (OS baseline, size, template)
Infra only. A **replace** dispatches the apps deploy for that host (flow A step 3). An in-place base change does not
redeploy apps; nightly L4 catches side effects.

### D. Destroy or role change
Destroy is infra Class C; the catalogue check then fails the deployment listing that host, so the same change set must
remove it from the deployment (apps Class C). The PM sequences both. A role-code change = rebuild (replace).

## 2.3 Locks

| Rule | Detail |
|---|---|
| File | `/var/lib/devops/locks/host-<hostname>.lock` on CT 300 (flock) |
| Who | infra apply (Ansible + tests part), apps deploy, apps verify, infra verify, patching (`ci-patch.sh`) |
| How | each run computes its host set, takes **all** host locks in sorted order **up front**, holds them for the whole run |
| Wait | 60 min, then the run fails loudly (`pipeline.failed`), never partially runs |
| Templates | existing `templates.lock` unchanged (shared by guest applies, exclusive for builds) |

## 2.4 Build from the catalogue ("PM, build Uptime Kuma in int")

| # | Step | Who | Gate |
|---|---|---|---|
| 1 | `plan_from_catalog(app, zone, count?)` — deterministic: hostnames (`hm1-<zone>-<role><nn>`), VMIDs (zone block), free IPs (static band), size and OS from the entry, pattern → number of guests, flows, secrets, the classes of each PR, and a **host RAM check** | PM | — |
| 2 | Refusal cases returned as reasons: entry `beta` outside stg, `deprecated`, zone without guests, RAM short, role code missing in standards | PM | — |
| 3 | Issue `build: <app> <zone>` with the plan (Faris sees it, can comment) | PM | — |
| 4 | Flows from `catalog.flows` not yet in `flows.yaml` → standards PR + UniFi | oc-net | B/C |
| 5 | `deployments/<zone>/<app>.yaml` | oc-cfg | **C** |
| 6 | Guest spec(s) sized from the entry | oc-infra | A (B in shr) |
| 7 | Apply → L1–L3 → dispatch → deploy → L4; probes generated (4.3) | pipeline | — |
| 8 | Report on the issue: endpoints, OpenBao path of the admin secret, test results; close the issue | PM | — |

Faris's only required action: approve the Class C deployment PR (and flows if Class C).

**Adding an app to the catalogue** is a separate request: oc-cfg writes the entry (`beta`) and a stg deployment, the
team reviews, Faris approves (new entry = C). `beta` → `stable` = Class C PR after **7 days green in stg** (L4 + probes).

## 2.5 Waves

- Every deployment has a `wave`; stg = 1 (enforced), others ≥ 2.
- A catalogue change (role, files, version) deploys wave by wave; wave N+1 starts only when every host of wave N passed L4.
- An entry with no stg deployment starts at wave 2 (as today). Deployments with their own `version` pin are skipped.

## 2.6 Daily timeline (MYT)

| Time | Job | Repo |
|---|---|---|
| continuous | probes (blackbox, every 60 s) → Alertmanager → OpenClaw incident | openclaw |
| 02:30 | drift: Terraform plan of every stack | infra |
| 03:00 | verify L1–L3, snapshot prune | infra |
| **03:15** | **verify L4 + app config drift, every deployment** | apps |
| 03:30 | OpenClaw nightly scan | openclaw |
| 03:40 / 09:40 / 15:40 / 21:40 | OS patch scan (+ Sev1/2 auto) | infra |
| Mon 04:00 | template refresh check | infra |
| **Mon 04:30** | **app version check** | apps |
| 25th / last Sat 02:00 | OS patch bundle / apply | infra |

## 2.7 Failure ownership

| Fails | Signal | First responder → owner |
|---|---|---|
| infra apply / L1–L3 | `verify: <host>` (infra), PR comment | SRE → oc-infra / oc-cfg |
| apps deploy / L4 / app drift | `app-verify: <app>/<host>` (apps), PR comment | SRE → oc-cfg |
| probe alert | Alertmanager → `openclaw-run incident` | SRE |
| dispatch infra → apps failed | infra PR comment + `pipeline.failed` | PM → oc-infra |
| version check found a release | `app update: <app> <version>` (apps) | PM → oc-cfg |

All issues opened by the pipeline are resolved and closed by the agents (`close_issue` with evidence); only Class C,
root or openclaw work goes to Faris.
