# Proposal: `homelab-apps` — application catalogue and deployments in their own repo

| | |
|---|---|
| Status | **WIP — revision 3.5, final confirmation** (2026-10-19). Nothing is built until all six engineers agree and Faris approves. |
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

## What changed in revision 3.5 (rev 3.3 round: reviewer agree with changes, 1 blocker)

| Point | Handled in |
|---|---|
| reviewer 3.3 (B) Jinja checked on the parsed AST: `attr` banned; `attribute=` only constant, no leading `_`; `Getattr`/`Getitem` rules; constants with `__`/`mro` rejected; one test per bypass | 5.1, 6 step 5 |
| reviewer 3.3 deployment values may not contain `__`/`mro` | 1.7 |
| reviewer 3.3 banned names also blocked in task/block/`include_role` `vars:`, role params, `include_vars`; `bao_create_secret` looks up zone/host from specs itself; test | 5.1, 6 step 5 |
| reviewer 3.3 guard checks every resolved `delegate_to` target (same deployment, not protected) | 1.7 |

## What changed in revision 3.4 (rev 3.3 round: configuration, sre, network, pm agree; infrastructure agree with changes, no blockers)

| Point | Handled in |
|---|---|
| infra 3.3-1 `ci-apps.pub` order: step 7a re-apply before the first check-mode run (and after step 8); failed apps clone keeps keys + warning + issue | 5.1, 6 step 7a |
| infra 3.3-2 patch/verify take the whole set in one `lock_hosts` call; locks released in `finish()`; 4b test with a held lock | 2.3, 6 step 4b |
| pm 3.3 reboot rule in the PM prompt switches to `reboot_guest` before cutover; `oc-pm` read on apps is enough to open issues | 5.3 |

## What changed in revision 3.3 (rev 3.2 round: infrastructure, sre, network agree; reviewer, configuration agree with changes)

| Point | Handled in |
|---|---|
| reviewer 3.2-1 (B) lint every `.j2` under `catalog/<app>/**` (incl. `files/**`) and any template src; unit test `files/sub/x.j2` | 5.1, 6 step 5 |
| reviewer 3.2-2 (B) reject `__` and `mro` in Jinja; one test per spelling | 5.1, 6 step 5 |
| cfg 3.2-B1 (B) `delegate_to` only to another host of the same deployment, guard re-checks; k3s keeps init-host token reads; `localhost` VIP check → `health`/`probes` | 5.1, 6 step 8 |
| cfg 3.2-B2 (B) `check_mode: false` only with `changed_when: false` on read-only modules | 5.1, 6 step 7 |
| cfg 3.2 clarification: `bao_create_secret` `generate`/`hash: bcrypt`, returns only the hash, `no_log`, no-op in check mode, existing secret = already created | 5.1 |
| reviewer 3.2-a guard-captured zone/host for `bao_create_secret`; no `set_fact`/`register` on banned keys | 5.1 |
| reviewer 3.2-b `include_vars`/`include_tasks`/`import_tasks` paths resolved statically | 5.1 |
| reviewer 3.2-c per-app policy: `create` only, explicit guest-zone mounts, with and without host | 5.1 |
| reviewer 3.2-d `VM.Snapshot` can delete any snapshot in those pools: accepted risk, recorded | 5.1 |
| reviewer 3.2-e restrict approvals to whitelist on apps `main` | 5.1 |
| reviewer 3.2-f lint failure = red check (cannot merge), not "Class C" | 5.1 |
| reviewer 3.2-g `ci-apps.pub` only on deployment hosts | 5.1 |
| reviewer 3.2-h Q12 tracked as its own infra issue | 6.3 |
| cfg 3.2 notes: `community.general` in `requirements.yml`; no leftover OpenBao refs in `app_adguard` defaults; version-check validates `version.url` and `known` keys; step 7 exception list | 6 steps 5, 7; 3.3 |

## What changed in revision 3.2 (confirmation round: pm, network agree; 4 × agree with changes)

Every point of the round, with where it is handled. (B) = was blocking.

| Point | Handled in |
|---|---|
| reviewer 12 (B) lint gate: `src:` paths resolve under `catalog/<app>/`, `fetch`/`synchronize`/`script` banned, every lookup/query spelling (FQCN, `query`, `q`, `with_<lookup>`), `.j2` scanned | 5.1 |
| reviewer 13 (B) no Jinja in deployment values, unit test | 1.7, 6 step 5 |
| reviewer 14 / cfg (B) precedence: deployment vars as generated group vars below inventory; banned keys incl. `zone`, `vmid`, `role`, `bao_*`; guard reads inventory values | 1.7 |
| reviewer 15 (B) one OpenBao policy + token role per app, child token per play, isolation test | 5.1, 5.4, 6 steps 4–5 |
| reviewer 16 / cfg B1 (B) pipeline-owned `bao_create_secret` (CT 300, create-only, path validated, owns env/token/password); roles migrated; no allow-lists | 5.1, 6 steps 7–8 |
| reviewer 18 / infra R3-1 (B) token wording; `ci-apps` push-blocked by rule `*`; no `.gitea/**` for agents; `oc-pm` no write, server-side token | 2.1, 5.3, 6 step 4 |
| infra F / reviewer 21 (B) locks dir `3770`, pre-created files, append open, never delete | 2.3, 6 step 4 |
| SRE B1 / net (B) drill on hm1-wifi-dns02 with Faris informed, `DNSQueryFailed` must stay silent; stg rollback untested | 4.7, 6 step 12 |
| reviewer 17 Proxmox: snapshot on deployment-zone pools only (not dmz → VM 210 unreachable), Rollback on stg only, `pre-deploy_*` names only | 5.1, 5.4 |
| reviewer 19 role `meta/**` C; matcher supports `{a,b}` | 5.2 |
| reviewer 20 `redeploy_app(from_wave)` guard | 2.5 |
| reviewer 22 `get_pr_status` evidence for apps PRs | 5.3 |
| reviewer notes: guard rejects `ansible_host` in 192.168.67.0/29; upstream checksum check for B bumps; `ci-apps` comments never reviews/Faris events; `environments.yaml` in the C row; empty wave 1 → wave 2 starts at once | 1.7, 5.2, 2.1, 2.5 |
| infra R3-2 `gitea_ci_token` read on apps; failed clone = warning | 2.1, 6 step 4 |
| infra R3-3 revert step 0: `APPS_DISPATCH=0` | 6 |
| infra R3-4 dispatch reads `$W/verify/verify.json` ∩ `VHOSTS` | 2.1 |
| infra R3-5 infra-dispatched single host runs outside wave order | 2.5 |
| cfg R3 role files `{{ role_path }}/../files`; `remote_src` exempt; `version.url` + `version.vars` mapping; iot wave 3; rewrite before first healthcheck; "0 changes" = no live-file/service change | 1.2, 1.5, 1.7, 1.9, 5.1, 6 step 7 |
| net R3 three UDM policies (tcp 22,80 · udp 53 · icmp); no OpenBao flow from guests; 1.9 purpose text derived from `router`; shr purpose excludes protected CTs; stale flows note + mgt /28 reconcile | 1.5, 1.9, 4.3, 5.4 |
| SRE R3-1 L4 keeps `example.com` query and adds the rewrite check | 1.5, 4.4b |
| SRE R3-2/3 silence matches `host` or `deployment`, expires when the reboot run ends | 3.4 |
| SRE R3-4 prune list incl. `pre-reboot_` | 2.6 |
| SRE R3-5 lock-wait timeout opens an issue | 2.3, 2.7 |
| SRE R3-6 revert window: alerts clear and not silenced | 6 |
| reviewer / cfg / infra stale text (RAM row, status line, canary wording) | 6.2, 2.5, this file |
| new Q12 (infra still lets oc-infra write `.gitea/*`) — follow-up, not part of this change | 6.3 |

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
