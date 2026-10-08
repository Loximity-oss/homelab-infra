# tests/ — guest deployment tests (CD testing, design-v7 decision 32)

Every guest the pipeline creates or changes is verified after apply; destroyed guests get a "gone" check.

| Layer | What | Where |
|---|---|---|
| L1 Platform | Proxmox API: guest matches its spec and zone | `scripts/verify.py` (runner) |
| L2 Reachability | ping, SSH, host key, hostname | `scripts/verify.py` (runner) |
| L3 OS baseline | Goss on the guest | `tests/os/<family>.goss.yaml.j2` via `ansible/roles/verify` |

Everything under `tests/` is **Class C** (Faris): the contract can't be weakened by an agent PR.
Scope today: Ubuntu LXC + VM. Apps get their own CD later; Windows later (Pester, same result format).
