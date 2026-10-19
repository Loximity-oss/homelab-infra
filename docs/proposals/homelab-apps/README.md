# Proposal: `homelab-apps` — application catalogue and deployments in their own repo

| | |
|---|---|
| Status | **WIP — revision 3.6, final confirmation** (2026-10-20). Nothing is built until all six engineers agree and Faris approves. |
| Asked by | Faris, 2026-10-19 |
| Review | infra issue #125: one `Verdict (<engineer>): agree \| agree with changes \| object` comment per engineer |
| Files | each under 18 000 characters, so `read_file` (max 20 000) returns it whole |

| File | Content |
|---|---|
| `README.md` | this index, summary, decisions, what changed in rev 2, review checklist |
| `1-boundary-catalogue.md` | what goes where; catalogue entries and deployments; `app.yaml` and deployment schema; `environments.yaml` (1.9) |
| `2-orchestration.md` | triggers, locks, flows (new guest, app change, **build from catalogue**), waves, timeline, failure ownership |
| `3-patching.md` | OS packages (infra) and app versions (apps), health gates, reboots |
| `4-verification.md` | L1–L4, k3s L4, continuous probes, client's-eye checks, the fix loop |
| `5-pipeline-classes.md` | apps pipeline, identities, change classes, secrets, OpenClaw and standards changes |
| `6-migration.md` | migration steps, rollback, risks, open questions |
| `CHANGELOG.md` | change tables of revisions 3.3 and earlier |

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

## What changed in revision 3.6 (rev 3.5 round: pm, infrastructure, sre, network agree; reviewer 4 blockers)

| Point | Handled in |
|---|---|
| reviewer 3.5-1 (B) `groups[...]` constant only; only `hostvars[<var>]` may take a variable | 5.1 |
| reviewer 3.5-2 (B) filter/test allow-list (`policy/jinja-allow.yaml`); string-named filters and `extract` rules | 5.1 |
| reviewer 3.5-3 (B) ansible-core ≥ 2.19 pinned on both runners, asserted in `deploy.yml` | 5.1, 6 steps 4–5 |
| reviewer 3.5-4 (B) `__`/`mro` rejected in every YAML scalar under `catalog/<app>/role/**` | 5.1 |
| reviewer 3.5-5 parse every expression context (`when`, `until`, `assert.that`, `loop`, module args) | 5.1 |
| reviewer 3.5-6 tests for 1–5 listed under step 5 | 6 step 5 |
| Claude: **privilege separation** so the lint is not the only boundary: deploy runs Ansible as `runner-apps-play` (no AppRole, Proxmox or Gitea credentials; only the run's child tokens + SSH key); PR render in `bwrap` (no network, no secrets); tests prove both | 5.1, 6 steps 4–5 |

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

Older revision tables (3.3, 3.2, 3, 2): `CHANGELOG.md`.

## Confirmation round (answer on #125: agree / agree with changes / object, with file and section)

| Engineer | Confirm |
|---|---|
| Infrastructure (oc-infra) | boundary (1), pipeline, identities, dispatch, locks (2.1–2.3, 5), classes (5.2), migration steps 2–4 and 7–8 buildable |
| Configuration (oc-cfg) | catalogue and deployment schema cover AdGuard and k3s fully (1), version and patching (3), check-mode equivalence (6 steps 5–6), build-from-catalogue steps 4 and 6 |
| Network (oc-net) | flows needed for probes and client's-eye checks (4), `catalog.flows` field and its use in build-from-catalogue (2.4), no zone changes |
| Reviewer (oc-rev) | classes fail closed (5.2); no app/deployment PR can reach Terraform, protected guests or secrets policy; evidence in PR comments suffices |
| SRE | failure ownership and timeline (2.6–2.7), reboot policies (3.4), probes and alerts (4), rollback of the cutover (6) |
| PM | flows 2.2–2.5 routable; tools (5.3); nothing needs agents beyond their scopes; `plan_from_catalog` output is enough to brief specialists |
