# tests/ — guest deployment tests (CD testing, design-v7 decision 32)

Every guest the pipeline creates or changes is verified after apply; destroyed guests get a "gone" check.

| Layer | What | Where |
|---|---|---|
| L1 Platform | Proxmox API: guest matches its spec and zone | `scripts/verify.py` (runner) |
| L2 Reachability | ping, SSH, host key, hostname | `scripts/verify.py` (runner) |
| L3 OS baseline | Goss on the guest | `tests/os/<family>.goss.yaml.j2` via `ansible/roles/verify` |
| L4 App checks | Goss on the guest, only for roles that have a file | `tests/apps/<role>.goss.yaml.j2` via `ansible/roles/verify` |

Run by hand (apply runner, plan token for L1):
`PROXMOX_VE_API_TOKEN=... scripts/verify.py --standards <dir> --hosts <h1,h2> --out <dir>` → `verify.md` + `verify.json`.
Results: ✅ pass · ❌ fail · ⚠️ skip (never shown as pass). Protected guests must be *invisible* to the pipeline token.

Everything under `tests/` is **Class C** (Faris): the contract can't be weakened by an agent PR.
Scope today: Ubuntu LXC + VM, Arch VM (`tests/os/arch.goss.yaml.j2`; btrfs root: inode check n/a), Fedora 44 VM (`tests/os/fedora44.goss.yaml.j2`; btrfs root, SELinux enforcing; named per release because `verify_os_family` only collapses ubuntu/debian/win). Windows later (Pester, same result format).

## L4 app checks

Optional layer keyed by the spec `role` code: `tests/apps/<role>.goss.yaml.j2`. Only guests whose role has a file get L4 rows; verify output for every other guest is unchanged.
- `ansible/roles/verify` renders the file to `/etc/hm1/goss/app.yaml` after the L3 result is written and runs Goss in a **separate run**, with its own result file `<host>.l4.json`. An app failure (or a render error) can neither hide nor break the L3 baseline result.
- A missing, unreadable or error result is a **failure** ("no result"), never a pass.
- Read-only, no credentials, loopback only.
- Values come from `ansible/playbooks/group_vars/role_<role>.yml` (loaded by the verify play). Role defaults that the verify play does not load are written as `app_x | default(<same literal>)`; keep them in sync with the role.
- Current file: `dns` = AdGuard Home (service, listeners, web UI, a DNS query, filter files, config drift).
