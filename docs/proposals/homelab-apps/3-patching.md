# 3. Patching — guests and apps

## 3.1 Who patches what

| Bits | Owner | How |
|---|---|---|
| OS packages (apt/dnf/pacman), incl. distro packages an app needs (e.g. `keepalived`) | infra | rings, unchanged (3.2) |
| App binaries pinned by the catalogue (AdGuard, k3s) | apps | version pin + bump PR (3.3) |
| Templates | infra | weekly refresh check → Class B refresh PR (works since infra #122) |
| PVE host | Faris | manual; dedicated patch user still to be built |
| Protected CTs 100/300/303 | unattended security updates 03:00, no reboot | unchanged |

## 3.2 OS packages — infra, unchanged except the health gate

Spec `patch: ring0|1|2`. Scan every 6 h; Sev1/2 applied automatically to ring 1 with a `pre-patch_*` snapshot; Sev3/4
in the monthly bundle PR (25th, Class C, Faris) applied last Saturday 02:00. Never reboots: opens
`patch: reboot <host>`. Takes the host locks (2.3).

**Change:** `patch_apply` drops its built-in DNS/k3s knowledge. After patching each host it runs that host's
catalogue `health` (services active + command, `timeout_s`) from a read-only `homelab-apps` main clone. Unhealthy →
rollout stops (`any_errors_fatal`), `pipeline.failed` → SRE. Host in no deployment, or apps clone unavailable → L3
basics only, plus a warning in the run.

## 3.3 App versions — apps

| Step | Detail |
|---|---|
| Pin | `version.pin` + `version.known[pin]` sha256; the role verifies the download on the guest |
| Check | Mon 04:30 (also validates that `version.url` contains `{version}` and each `known` key matches the URL asset name, cfg 3.2): upstream latest vs pin → issue `app update: <app> <version>` with the **new sha256** (AdGuard: release asset; k3s: `sha256sum-amd64.txt`, line `k3s`) and the release-notes link; security releases flagged `Sev2`. Also lists deployments whose `pin_until` has passed |
| Bump PR | oc-cfg changes `version.pin` and adds the `known` entry. patch/minor = `bump_class` (B default); major = C; k3s = C always |
| Roll out | waves (2.5): wave 1 (stg) → L4 green → `wave_delay` → wave 2; host by host with `health` between hosts; snapshot `pre-deploy_*` per host |
| k3s | `rollout.groups` hooks: drain → upgrade → uncordon → quorum check, control nodes first, one at a time |
| Hold back | a deployment pins an older `known` version with `pin_until` (B) |
| Urgent (Sev1/2 release) | same PR; PM asks the Reviewer (or Faris for C) at once; no auto-merge |
| Time window | none enforced: Class C bumps deploy when Faris merges; Class B when the Reviewer merges |

## 3.4 Reboots

Rule unchanged: **every guest reboot waits for Faris** (`reboot` comment on `patch: reboot <host>`, openclaw #25).
**New: reboots run as an infra workflow, not as a direct agent power action.** The PM calls `reboot_guest(issue)`,
which dispatches infra `reboot.yml` (inputs: issue number). The workflow (main's code, `host-apply`):

1. reads the issue and **checks in code** that its newest answer is a `reboot` comment authored by `faris`
   (the prompt-only check of openclaw #25 becomes a hard gate); refuses protected VMIDs and hosts without a spec;
2. takes the host lock (2.3). Probe silence: `reboot_guest` (on CT 100, next to Alertmanager) creates a silence
   matching `host=<host>` **or** `deployment=<deployment>` (k3s alerts carry the deployment label) for at most
   `reboot.timeout_s`, waits for the workflow, and **expires the silence as soon as the run ends** (green or not), so a
   fast reboot never masks later failures (SRE R3-2, R3-3);
3. snapshot `pre-reboot_<ts>`; runs the catalogue policy below over SSH; reboots via the Proxmox apply token;
4. waits for `health`; comments the result; closes the issue on success (or leaves it open with the failure).

Policy from the host's catalogue entry:

| Policy | Behaviour |
|---|---|
| `one_at_a_time` | snapshot → reboot → wait for `health` (≤ `timeout_s`) → done |
| `pair` | refuse unless the partner passes `health` now; reboot; wait for `health` on **both** hosts |
| `drain` | `before` as written in the entry (`kubectl drain …`, ≤ 300 s, run on the first control node) → reboot → `health` → `after` (`kubectl uncordon`). Never two control nodes at once |

Failure path: a step times out or `after` fails → stop; the node stays as it is (cordoned for k3s); the workflow
opens an `app-verify: <app>/<host>` issue (SRE triages); no other host of that deployment is rebooted until it is
closed. `pre-reboot_*` snapshots follow the 7-day prune (3.5).

Hosts in no deployment: `one_at_a_time`. PVE host: manual (Faris).

## 3.5 Snapshots

| Prefix | Taken by | Kept | Pruned by |
|---|---|---|---|
| `pre-apply_*` | infra apply | 7 days | 03:00 verify (`snapshot.py prune`) |
| `pre-patch_*` | patching | 7 days | same job, prefix added |
| `pre-deploy_*` | apps deploy (`svc-apps@pve`) | 7 days | same job, prefix added |
| `pre-reboot_*` | infra `reboot` workflow | 7 days | same job, prefix added |
| `pre-change_*`, `manual_*` | SRE | until removed by SRE | — |
| `pre-cutover_*` | SRE at step 10(a) | until the SRE removes them after a green night | never pruned automatically (SRE 3.7-4) |

Rollback of a failed deploy: stg automatic (failed L4 hosts); other zones **manual** by the SRE from `pre-deploy_*`
(runbook row), then the PM opens a revert PR. k3s control nodes are restored as a set after `k3s etcd-snapshot save`.

## 3.6 Calendar

| When | OS (infra) | Apps |
|---|---|---|
| every 6 h | scan, Sev1/2 auto | — |
| Mon | template refresh check 04:00 | version check 04:30 |
| 25th | bundle PR | — |
| last Sat 02:00 | approved bundle | — |
| on merge | — | bump PRs deploy in waves |
