# tests/ — guest deployment tests (CD testing, design-v7 decision 32)

Every guest the pipeline creates or changes is verified after apply; destroyed guests get a "gone" check.

| Layer | What | Where |
|---|---|---|
| L1 Platform | Proxmox API: guest matches its spec and zone | `scripts/verify.py` (runner) |
| L2 Reachability | ping, SSH, host key, hostname | `scripts/verify.py` (runner) |
| L3 OS baseline | Goss on the guest | `tests/os/<family>.goss.yaml.j2` via `ansible/roles/verify` |

Run by hand (apply runner, plan token for L1):
`PROXMOX_VE_API_TOKEN=... scripts/verify.py --standards <dir> --hosts <h1,h2> --out <dir>` → `verify.md` + `verify.json`.
Results: ✅ pass · ❌ fail · ⚠️ skip (never shown as pass). Protected guests must be *invisible* to the pipeline token.

Everything under `tests/` is **Class C** (Faris): the contract can't be weakened by an agent PR.
Scope today: Ubuntu LXC + VM, Arch VM (`tests/os/arch.goss.yaml.j2`; btrfs root: inode check n/a). Apps get their own CD later; Windows later (Pester, same result format).
