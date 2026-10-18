# k3s cluster — runbook

Hosts: `hm1-shr-k3sc01..03` (VMID 310-312), `hm1-shr-k3sw01..02` (VMID 313-314). Role `svc_k3s`, files in `apps/k3s/`. API VIP 192.168.70.200:6443. Design: `docs/apps/k3s.md`.

## Check
On a control node (`ssh_exec` on 310/311/312):
- `systemctl is-active k3s keepalived` (workers: `k3s-agent`)
- `k3s kubectl get nodes -o wide` (5 nodes `Ready`; servers show `control-plane,etcd`)
- `k3s kubectl get --raw '/readyz?verbose' | tail -5`
- `k3s etcd-snapshot ls` (snapshots), `ip -4 addr show | grep 192.168.70.200` (which node holds the VIP)
- `curl -ks -o /dev/null -w '%{http_code}\n' https://192.168.70.200:6443/livez` from any shr guest: expect `401` (anonymous auth is off; 401 means the API answers)
- `/usr/local/bin/k3s-api-check; echo $?` (0 = healthy, the keepalived check)
- `journalctl -u k3s -n 80 --no-pager` (workers: `-u k3s-agent`)
- Escrow done: `test -f /etc/hm1/k3s-tokens-escrowed && cat /etc/hm1/k3s-tokens-escrowed` on k3sc01

## Common problems
- **VIP not answering**: `systemctl status keepalived` and `journalctl -u keepalived` on the control nodes; a node in VRRP FAULT means `/usr/local/bin/k3s-api-check` fails (API not serving). VRRP is IP protocol 112 between the control nodes. Nodes still reach each other by IP; only workers and kubectl use the VIP. Do not enable `anonymous-auth` to "fix" a 401: 401 is the healthy answer.
- **A control node crash-loops with `server/cred/passwd newer than datastore and could cause a cluster outage`**: see the next section.
- **A control node does not join**: `journalctl -u k3s` on it; check it reaches `192.168.70.30:6443` and 2379-2380 between servers, clocks in sync, and `/etc/rancher/k3s/config.yaml` has `server:` and `token:` (the server token). Re-run the apply (idempotent).
- **Worker stays NotReady / does not join**: reach `192.168.70.200:6443`, check `br_netfilter`/`overlay` (`lsmod`), `sysctl net.ipv4.ip_forward` = 1, swap off (`swapon --show` empty), `journalctl -u k3s-agent`. A worker's `token:` must be the agent token (`/etc/rancher/k3s/agent-token` on k3sc01); the server token does not belong on workers.
- **Pods stuck Pending on all workers**: taint is intended on servers only; check `kubectl describe node`.
- **Lost quorum (2 of 3 control nodes down)**: API unavailable. Bring the nodes back first. Restoring from a snapshot is a last resort: `k3s server --cluster-reset --cluster-reset-restore-path=<snapshot>` on one node, then wipe `/var/lib/rancher/k3s/server/db` on the others and rejoin (Faris decides).
- **The last play 'k3s token escrow' failed at 'Write once to OpenBao'**: the cluster itself is complete (the play runs after it). 403 = the ci-apply policy lacks `+/secret/data/apps/k3s/*` create, or the shr namespace is missing (platform: Faris, `apply.sh`). No marker was written, so the next apply retries. **400 (check-and-set)** = the secret already exists in OpenBao but the marker is missing (the run died between write and marker): Faris deletes `shr/secret/apps/k3s/hm1-shr-k3sc01` in OpenBao, then re-run the apply.

## Crash loop: `cred/passwd newer than datastore`
Cause: k3s writes `/var/lib/rancher/k3s/server/cred/passwd` itself while a node joins (or when credentials change). Its mtime is then newer than the datastore's bootstrap record and k3s refuses every later start (checked at start only). Typical triggers: all control nodes restarted together (host reboot), a crash, or a restart after a binary/config/unit change. The unit of joining control nodes (k3sc02/03) removes the file before every start (`ExecStartPre`), so this should no longer happen there.

Manual recovery (SRE), one control node at a time, never two together (etcd quorum):
1. Snapshot the VM first (`pre-change` snapshot) and, if the API still answers on another node, `k3s etcd-snapshot save` there.
2. On the failing node: `systemctl stop k3s`, `mv /var/lib/rancher/k3s/server/cred/passwd /root/passwd.bak`, `systemctl start k3s`; k3s recreates it from the datastore.
3. Wait for `k3s kubectl get nodes` to show the node `Ready` and `/readyz` ok before the next node.
4. Start with the node whose etcd can form quorum; k3sc01 (datastore source) is moved aside the same way only if it shows the same fatal.
5. Do not delete the file on a healthy, running node.
The role also removes it for a joining control node that is not running during an apply, and never restarts k3s straight after its first start.

## Restart rule (role)
The role restarts k3s only if it was already running before the run started and its binary, config or unit changed. A first start is never followed by a restart. Combined with `serial: 1` this gives a rolling restart, one node at a time.

## Replacing a node
- **Worker**: `kubectl drain <w> --ignore-daemonsets --delete-emptydir-data`, `kubectl delete node <w>`, then rebuild the VM; the apply joins it with the agent token from `k3sc01`.
- **Control node other than k3sc01**: `kubectl delete node <c>` and remove its etcd member, rebuild, apply.
- **k3sc01 (first sorted host)**: do not rebuild blindly. A fresh k3sc01 would run `cluster-init` and start a new cluster, and generate a new agent token. Instead make it join: first temporarily set `svc_k3s_init_host` to another healthy control node by PR (Faris approves), copy `/etc/rancher/k3s/agent-token` to that node, rebuild, apply, revert.

## Patching and reboots
All nodes are `ring1`. Reboots wait for Faris. One control node at a time: `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data`, reboot, wait `Ready`, `kubectl uncordon`, check `get nodes` and `/readyz`, then the next. Never two control nodes together. Workers one at a time, drained first. A host reboot restarts all guests at once: see the crash loop section.

## Upgrade / rollback
Bump `svc_k3s_version` and `svc_k3s_sha256` in `ansible/roles/svc_k3s/defaults/main.yml` by PR (checksum from the release's `sha256sum-amd64.txt`, line `k3s`; step one minor version at a time). The apply upgrades control nodes then workers, one at a time, stopping on the first node that is not Ready. Take an etcd snapshot first (`k3s etcd-snapshot save`). Rollback of a minor upgrade needs a snapshot restore.

## Tokens
Two different secrets, both root-only on the guests, never in Git or logs:
- **Server token** `/var/lib/rancher/k3s/server/token` on the control nodes (also in `config.yaml` of `k3sc02/03`). Lets a node join as a control node.
- **Agent token** `/etc/rancher/k3s/agent-token` on `k3sc01` (generated once), set as `agent-token` in `config.yaml` on all control nodes and as `token:` on the workers. Lets a node join as a worker only.

**Escrow in OpenBao.** Both tokens are copied once to `shr/secret/apps/k3s/hm1-shr-k3sc01` (keys `server_token`, `agent_token`) by the last play of the apply (`tasks/escrow.yml`), guarded by `/etc/hm1/k3s-tokens-escrowed` (root, 0600) on k3sc01. The write is create-only: the pipeline can never read, overwrite or update it, and agents see key names only. Faris reads it in OpenBao (namespace `shr`, `secret/apps/k3s/hm1-shr-k3sc01`). Use it to rebuild or re-seed a control node after a total loss of the cluster nodes (the etcd data itself is not in it).

**Rotation.** The escrow is a one-time copy and is not updated by the pipeline. After rotating a token: Faris deletes the secret (all versions) in OpenBao, deletes `/etc/hm1/k3s-tokens-escrowed` on k3sc01, then re-run the apply, which writes the new values.
- Agent token: delete `/etc/rancher/k3s/agent-token` on k3sc01 and re-run the apply (control nodes restart one at a time, workers are re-configured and restarted), then redo the escrow as above.
- Server token: `k3s token rotate` on a server, then re-run the apply, then redo the escrow as above.

## Data / backup
etcd and snapshots live in `/var/lib/rancher/k3s/server/db`; **not backed up** (open item A-1). Config, binary and units are rebuilt by the apply; the two tokens are escrowed in OpenBao (agent token is regenerated if lost and no escrow exists).
