# Proposal: `homelab-apps` — application catalogue and deployments in their own repo

| | |
|---|---|
| Status | **WIP — revision 2, team review** (2026-10-19). Nothing is built until all six engineers agree and Faris approves. |
| Asked by | Faris, 2026-10-19 |
| Review | infra issue #125: one `Verdict (<engineer>): agree \| agree with changes \| object` comment per engineer |
| Files | each under 15 000 characters so `read_file` returns it whole (rev 1 was cut at 20 000) |

| File | Content |
|---|---|
| `README.md` | this index, summary, decisions, what changed in rev 2, review checklist |
| `1-boundary-catalogue.md` | what goes where; catalogue entries and deployments; `app.yaml` and deployment schema |
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

## What changed in revision 2

| From | Change | Where |
|---|---|---|
| Faris | catalogue (`catalog/`) + deployments (`deployments/<zone>/`); statuses example/beta/stable/deprecated; `list_catalog`, `plan_from_catalog`; "build from catalogue" flow | 1, 2, 5 |
| Faris | rollout waves (stg first), per-deployment version pin | 1, 2, 3 |
| Faris | verification: continuous probes, client's-eye checks, k3s L4 | 4 |
| oc-infra #1 | locks specified: each pipeline takes all its host locks sorted, up front, held for the run; added to `ci-apply.sh`, `ci-patch.sh`, `ci-verify.sh`, apps deploy/verify | 2.3, 6 step 7 |
| oc-infra #2 | dispatch only for hosts that passed L1–L3, inserted after the verify block of `ci-apply.sh` before `finish`; `workflow_dispatch` is already used by `verify_guest` and `build_template`; apps reports to infra PRs with the `ci` bot token (comment rights on both repos) | 2.1, 5.1 |
| oc-infra #3 | targets are explicit deployment files; adding/removing hosts or a deployment = **Class C** | 5.2 |
| oc-infra #4 | apps classifier defaults to **C** for unmatched paths; field-level judgement by base/head leaf comparison (like `template_change()`); globs anchored per segment | 5.2 |
| oc-infra #5 | L4 leaves infra at cutover: `verify.py` (`app_tests`), `ansible/roles/verify` L4 tasks, `tests-check.py`, `tests/apps/` | 6 step 8 |
| oc-infra | proposal split into files under the `read_file` limit | this file |

## Review checklist (answer on #125: agree / agree with changes / object, with file and section)

| Engineer | Confirm |
|---|---|
| Infrastructure (oc-infra) | boundary (1), pipeline, identities, dispatch, locks (2.1–2.3, 5), classes (5.2), migration steps 2–4 and 7–8 buildable |
| Configuration (oc-cfg) | catalogue and deployment schema cover AdGuard and k3s fully (1), version and patching (3), check-mode equivalence (6 steps 5–6), build-from-catalogue steps 4 and 6 |
| Network (oc-net) | flows needed for probes and client's-eye checks (4), `catalog.flows` field and its use in build-from-catalogue (2.4), no zone changes |
| Reviewer (oc-rev) | classes fail closed (5.2); no app/deployment PR can reach Terraform, protected guests or secrets policy; evidence in PR comments suffices |
| SRE | failure ownership and timeline (2.6–2.7), reboot policies (3.4), probes and alerts (4), rollback of the cutover (6) |
| PM | flows 2.2–2.5 routable; tools (5.3); nothing needs agents beyond their scopes; `plan_from_catalog` output is enough to brief specialists |
