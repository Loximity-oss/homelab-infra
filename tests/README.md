# tests/ — guest deployment tests (CD testing, design-v7 decision 32)

Every guest the pipeline creates or changes is verified after apply; destroyed guests get a "gone" check.

| Layer | What | Where |
|---|---|---|
| L1 Platform | Proxmox API: guest matches its spec and zone | `scripts/verify.py` (runner) |
| L2 Reachability | ping, SSH, host key, hostname | `scripts/verify.py` (runner) |
| L3 OS baseline | Goss on the guest | `tests/os/<family>.goss.yaml.j2` via `ansible/roles/verify` |
| L4 App checks | moved to **homelab-apps** (migration step 10(c), infra #127) | `catalog/<app>/tests/l4.goss.yaml.j2`, run by apps `verify` (03:15) and after each deploy wave |

Run by hand (apply runner, plan token for L1):
`PROXMOX_VE_API_TOKEN=... scripts/verify.py --standards <dir> --hosts <h1,h2> --out <dir>` → `verify.md` + `verify.json`.
Results: ✅ pass · ❌ fail · ⚠️ skip (never shown as pass). Protected guests must be *invisible* to the pipeline token.

Everything under `tests/` is **Class C** (Faris): the contract can't be weakened by an agent PR.
Scope today: Ubuntu LXC + VM, Arch VM (`tests/os/arch.goss.yaml.j2`; btrfs root: inode check n/a), Fedora 44 VM (`tests/os/fedora44.goss.yaml.j2`; btrfs root, SELinux enforcing; named per release because `verify_os_family` only collapses ubuntu/debian/win). Windows later (Pester, same result format).

## L4 app checks

Moved to homelab-apps at cutover (migration step 10(c), infra #127): each catalogue entry has
`catalog/<app>/tests/l4.goss.yaml.j2`, run by `playbooks/verify.yml` through the apps runner (nightly 03:15, after each
apply wave, on dispatch). Failures open `app-verify: <app>/<host>` issues there. This repo's verify is L1-L3 only.
