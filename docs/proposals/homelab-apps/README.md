# Proposal: `homelab-apps` — application catalogue and deployments in their own repo

| | |
|---|---|
| Status | **WIP — revision 3, confirmation round** (2026-10-19). Nothing is built until all six engineers agree and Faris approves. |
| Asked by | Faris, 2026-10-19 |
| Review | infra issue #125: one `Verdict (<engineer>): agree \| agree with changes \| object` comment per engineer |
| Files | each under 15 000 characters, so `read_file` (max 20 000) returns it whole |

| File | Content |
|---|---|
| `README.md` | this index, summary, decisions, what changed in rev 2, review checklist |
| `1-boundary-catalogue.md` | what goes where; catalogue entries and deployments; `app.yaml` and deployment schema; `environments.yaml` (1.9) |
| `2-orchestration.md` | triggers, locks, flows (new guest, app change, **build from catalogue**), waves, timeline, failure ownership |
| `3-patching.md` | OS packages (infra) and app versions (apps), health gates, reboots |
| `4-verification.md` | L1–L4, k3s L4, continuous probes, client's-eye checks, the fix loop |
| `5-pipeline-classes.md` | apps pipeline, identities, change classes, secrets, OpenClaw and standards changes |
| `6-migration.md` | migration steps, rollback, risks, open questions |

## Summary

1. **`homelab-infra` keeps the guest**: Terraform, templates, guest specs, OS baseline, OS patching, L1–L3, host helpers.
2. **`homelab-apps` holds a catalogue and deployments.** `catalog/<app>/` = what an app is (role, files, tests, docs,
   requirements, version). `deployments/<zone>/<app>.yaml` = where an instance runs (explicit hosts, config, wave).
3. **Guest specs stay the only source of hosts.** A deployment names hosts; the pipeline checks each exists in infra with
   the catalogue's role code and the deployment's zone. Protected guests have no spec, so they can never be targeted.
4. **Orchestration is one-way:** infra apply → guest L1–L3 green → dispatches the apps deploy for that guest.
   Per-host locks serialise apply, deploy, patching and verify.
5. **Waves:** stg deployments are wave 1; other zones follow only when wave 1 is green (L4). A deployment can hold an
   older version (`version: <pin>`).
6. **Patching:** OS packages = infra rings (unchanged). App versions = catalogue pin, weekly upstream check, bump PR,
   rolled out in waves. Reboots stay Faris-approved and follow each app's reboot policy.
7. **Verification:** L1–L3 (infra) + L4 (apps) on every change and nightly; **new** continuous probes (Prometheus
   blackbox, generated from specs + catalogue) and client's-eye checks; **new** k3s L4.
8. **Build from catalogue:** "PM, build X in zone Z" → `plan_from_catalog` computes hosts/IPs/size/flows/secrets →
   one issue with the plan → flows, deployment (Class C, Faris), guest specs → apply → deploy → report.
9. **Migration without a gap:** the apps pipeline runs `--check --diff` (must be 0 changes on live hosts) until one
   cutover PR set switches ownership.

## Decisions taken in the discussion with Faris (2026-10-19)

| Decision | Detail |
|---|---|
| One repo per layer, not per environment | stg and int are folders (`deployments/<zone>/`) and waves, not repos. `stg` = test-first zone; `int` = internal production trust zone, not a test tier |
| Catalogue + deployments | Helm-like split: definition vs instance; several instances per app; explicit targets |
| Agents resolve their issues | Faris approves Class C, root and `homelab/openclaw` work only |
| Narrow tools / root jobs, never wider tokens | e.g. `svc-apps@pve` can only snapshot; no DevOpsAdmin in apps |
| Kubernetes workloads | parked; later `clusters/<zone>` GitOps layout; more host RAM before an int cluster |

## What changed in revision 3 (round-2 verdicts: 6 × agree with changes)

| From | Change | Where |
|---|---|---|
| reviewer | **separate runner `host-apps` / user `runner-apps`**, own SSH key and AppRole (Q1 decided); per-app OpenBao create lines; distinct `ci-apps` bot; pool check in nightly scan | 5.1, 5.4 |
| reviewer, oc-infra | PR job offline only (`pull_request_target`, `host-plan`); lint gate for dangerous constructs; deploy guard in `pre_tasks`; `vars` key ban; dispatch only from `main`, inputs untrusted | 2.1, 2.2 B, 5.1 |
| reviewer, oc-cfg, oc-infra | classifier: segment matcher with `**`; every unlisted leaf C; full field list; stricter-of for `bump_class`; renames, symlinks, plugin dirs, root config = C; unit tests; docs-only merge never deploys; `health` and rollout gates = C | 5.2 |
| oc-infra | locks: `lock_hosts` helper, taken before the pre-apply snapshot, set = all touched hosts, scan none, `patch_run`, verify fleet, job timeouts 240 min; dispatch only hosts with no failed test row, nothing if Ansible failed; dispatch failure = warning + issue; `APPS_DISPATCH` in `apply.yml`, enabled in a separate PR; revert order reversed; locks before check-mode runs | 2.1, 2.3, 6 |
| PM | tracking issue per multi-PR change with PR checklist; SRE verify step in every flow; remove flow (deployment first, then destroy); `plan_from_catalog` briefs + `create_issue` + `mode=remove`; `repo="apps"` everywhere; `app_verify_status`; wave-failure semantics + `redeploy_app(from_wave)` | 2.2, 2.4, 2.5, 5.3 |
| SRE | `health` contract (shell, exit 0, timeout, no secrets) with real commands; probe record `probe.hm1.lox-internal.dev` (no WAN dependency); snapshot retention table; reboot silence, pair/drain failure paths; inhibit rules; drill 5 min; k3s L4 per role + VIP holder deployment-level; `wave_delay`; cutover snapshot + etcd snapshot + revert trigger 30 min; runbooks | 1.5, 3, 4, 6 |
| oc-cfg | `version.known` checksums, k3s build metadata = patch; secrets schema with real keys; missing host = warning; `deploy.yml` builds `role_<code>` groups from the deployment; `role_path`-relative files; k3s upgrade hooks + quorum check; **stg AdGuard canary** (`hm1-stg-dns01`); `canary_waiver`; `playbooks/**` to oc-cfg; peer check renamed; read-only reads infra → apps stated | 1, 2, 3, 6 |
| oc-net | flow vocabulary (`self-zone`, zones, hosts, `internet`); flows resolved per router; narrow UDM policy (CT 100 → 4 DNS IPs: 53/udp, 80/tcp, icmp, 22/tcp, Faris creates); mgt → shr is `flows.yaml` only; gate step 4b before apply in gateway zones; planned IP = spec IP; `flows.yaml` schema first | 1.8, 2.4, 4.3, 5.4 |
| Claude | **reboots become an infra `reboot` workflow**: Faris's `reboot` comment is checked in code, host lock taken, catalogue policy, `pre-reboot_*`; `reboot_guest` PM tool. **Step 0 vzdump** of all non-protected guests except VM 210 (never touched) | 3.4, 6 |
| found in review | `comment_issue` truncates at 6 000 chars (reviewer's verdict was cut at point 12) → fix in step 2 | 5.3 |

## Confirmation round (answer on #125: agree / agree with changes / object, with file and section)

| Engineer | Confirm |
|---|---|
| Infrastructure (oc-infra) | boundary (1), pipeline, identities, dispatch, locks (2.1–2.3, 5), classes (5.2), migration steps 2–4 and 7–8 buildable |
| Configuration (oc-cfg) | catalogue and deployment schema cover AdGuard and k3s fully (1), version and patching (3), check-mode equivalence (6 steps 5–6), build-from-catalogue steps 4 and 6 |
| Network (oc-net) | flows needed for probes and client's-eye checks (4), `catalog.flows` field and its use in build-from-catalogue (2.4), no zone changes |
| Reviewer (oc-rev) | classes fail closed (5.2); no app/deployment PR can reach Terraform, protected guests or secrets policy; evidence in PR comments suffices |
| SRE | failure ownership and timeline (2.6–2.7), reboot policies (3.4), probes and alerts (4), rollback of the cutover (6) |
| PM | flows 2.2–2.5 routable; tools (5.3); nothing needs agents beyond their scopes; `plan_from_catalog` output is enough to brief specialists |
