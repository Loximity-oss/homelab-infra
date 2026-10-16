# k3s cluster — runbook

Hosts: `hm1-shr-k3sc01..03` (VMID 310-312), `hm1-shr-k3sw01..02` (VMID 313-314). Role `svc_k3s`, files in `apps/k3s/`. API VIP 192.168.70.200:6443. Design: `docs/apps/k3s.md`.

## Check
On a control node (`ssh_exec` on 310/311/312):
- `systemctl is-active k3s keepalived` (workers: `k3s-agent`)
- `k3s kubectl get nodes -o wide` (5 nodes `Ready`; servers show `control-plane,etcd`)
- `k3s kubectl get --raw /readyz?verbose | tail -5`
- `k3s etcd-snapshot ls` (snapshots), `ip -4 addr show | grep 192.168.70.200` (which node holds the VIP)
- `curl -ks https://192.168.70.200:6443/livez` from any shr guest
- `journalctl -u k3s -n 80 --no-pager` (workers: `-u k3s-agent`)

## Common problems
- **VIP not answering**: `systemctl status keepalived` on the control nodes; the health script needs `/livez` on 127.0.0.1:6443. VRRP is IP protocol 112 between the control nodes; check `journalctl -u keepalived`. Nodes still reach each other by IP, only workers and kubectl use the VIP.
- **A control node does not join**: `journalctl -u k3s` on it; check it reaches `192.168.70.30:6443` and 2379-2380 between servers, clocks in sync, and `/etc/rancher/k3s/config.yaml` has `server:` and `token:`. Re-run the apply (idempotent).
- **Worker stays NotReady / does not join**: reach `192.168.70.200:6443`, check `br_netfilter`/`overlay` (`lsmod`), `sysctl net.ipv4.ip_forward` = 1, swap off (`swapon --show` empty), `journalctl -u k3s-agent`.
- **Pods stuck Pending on all workers**: taint is intended on servers only; check `kubectl describe node`.
- **Lost quorum (2 of 3 control nodes down)**: API read-only/unavailable. Bring the nodes back first. Restoring from a snapshot is a last resort: `k3s server --cluster-reset --cluster-reset-restore-path=<snapshot>` on one node, then wipe `/var/lib/rancher/k3s/server/db` on the others and rejoin (Faris decides).

## Replacing a node
- **Worker**: `kubectl drain <w> --ignore-daemonsets --delete-emptydir-data`, `kubectl delete node <w>`, then rebuild the VM; the apply joins it with the token from `k3sc01`.
- **Control node other than k3sc01**: `kubectl delete node <c>` and remove its etcd member (`k3s kubectl get nodes`, then on a healthy server `k3s etcd-snapshot` first, member removal happens on node delete), rebuild, apply.
- **k3sc01 (first sorted host)**: do not rebuild blindly. A fresh k3sc01 would run `cluster-init` and start a new cluster. Instead make it join: first temporarily set `svc_k3s_init_host` to another healthy control node by PR (Faris approves), rebuild, apply, revert.

## Patching and reboots
All nodes are `ring1`. Reboots wait for Faris. One control node at a time: `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data`, reboot, wait `Ready`, `kubectl uncordon`, check `get nodes` and etcd (`/readyz`), then the next. Never two control nodes together. Workers one at a time, drained first.

## Upgrade / rollback
Bump `svc_k3s_version` and `svc_k3s_sha256` in `ansible/roles/svc_k3s/defaults/main.yml` by PR (checksum from the release's `sha256sum-amd64.txt`, line `k3s`; step one minor version at a time). The apply upgrades control nodes then workers, one at a time, stopping on the first node that is not Ready. Take an etcd snapshot first (`k3s etcd-snapshot save`). Rollback of a minor upgrade needs a snapshot restore.

## Token
The join token is `/var/lib/rancher/k3s/server/token` on the control nodes (root only); also in `/etc/rancher/k3s/config.yaml` on joined nodes. It is never in Git or logs. Rotation: `k3s token rotate` on a server, then re-run the apply so the config files pick up the new value.

## Data / backup
etcd and snapshots live in `/var/lib/rancher/k3s/server/db`; **not backed up** (open item A-1). Config, binary and units are rebuilt by the apply.
