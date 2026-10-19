# 3. Patching — guests and apps

## 3.1 Who patches what

| Bits | Owner | How |
|---|---|---|
| OS packages (apt/dnf/pacman), incl. distro packages an app needs (e.g. `keepalived`) | infra | rings, unchanged (3.2) |
| App binaries / images pinned by the catalogue (AdGuard, k3s) | apps | version pin + bump PR (3.3) |
| Templates | infra | weekly refresh check → Class B refresh PR (works since infra #122) |
| PVE host | Faris | manual; dedicated patch user still to be built |
| Protected CTs 100/300/303 | unattended security updates 03:00, no reboot | unchanged |

## 3.2 OS packages — infra, unchanged

Spec `patch: ring0|1|2`. Scan every 6 h; Sev1/2 applied automatically to ring 1 with a `pre-patch_*` snapshot; Sev3/4
in the monthly bundle PR (25th, Class C, Faris) applied last Saturday 02:00. Never reboots: opens
`patch: reboot <host>`.

**Change:** `patch_apply` stops having DNS/k3s knowledge. After patching each host it runs the host's catalogue
`health` (services active + command, timeout) from a read-only `homelab-apps` main clone. Unhealthy → rollout stops
(`any_errors_fatal`), `pipeline.failed` → SRE. Hosts without a deployment get only the L3 basics.

## 3.3 App versions — apps

| Step | Detail |
|---|---|
| Pin | `version.pin` + `sha256` in the entry; the role verifies the download on the guest (as today) |
| Check | Mon 04:30: upstream latest vs pin → issue `app update: <app> <version>` with release-notes link; security releases flagged `Sev2` in the title |
| Bump PR | oc-cfg changes `version.*` only. patch/minor = `bump_class` (B default); major = C; k3s = C always |
| Roll out | waves (2.5): stg first, L4 green, then the rest; snapshot `pre-deploy_*` per host |
| Hold back | a deployment sets `version: <old pin>` (B), e.g. during a freeze |
| Urgent (Sev1/2 release) | same PR; PM asks the Reviewer at once; no auto-merge |

## 3.4 Reboots

Rule unchanged: **every guest reboot waits for Faris** (`reboot` comment on `patch: reboot <host>`, openclaw #25).
New: the reboot run reads the host's catalogue `reboot` block:

| Policy | Behaviour |
|---|---|
| `one_at_a_time` | snapshot → reboot → wait for `health` → done |
| `pair` | refuse while the other host of the same deployment is unhealthy or rebooting |
| `drain` | `before` (e.g. `kubectl drain`) → reboot → `health` → `after` (`kubectl uncordon`); never two k3s control nodes at once |

Hosts without a deployment: `one_at_a_time`. PVE host: manual (Faris).

## 3.5 Calendar

| When | OS (infra) | Apps |
|---|---|---|
| every 6 h | scan, Sev1/2 auto | — |
| Mon | template refresh check 04:00 | version check 04:30 |
| 25th | bundle PR | — |
| last Sat 02:00 | approved bundle | — |
| any day 08:00–22:00 MYT | — | bump PRs deploy on merge, in waves |
