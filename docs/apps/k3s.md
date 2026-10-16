# k3s HA cluster — design note (Configuration Engineer)

| | |
|---|---|
| Guests / zone | zone shr, VMs cloned from template 9000 (Ubuntu 26.04). Control (role `k3sc`, group `role_k3sc`): `hm1-shr-k3sc01..03` (VMID 310-312, 192.168.70.30-.32, 2 vCPU / 4 GB / 20 GB, `lvmt-nvme01`). Workers (role `k3sw`, group `role_k3sw`): `hm1-shr-k3sw01..02` (VMID 313-314, .33-.34, 4 vCPU / 6 GB / 40 GB, `lvmt-sata01`) |
| VMs, not LXC | k3s needs kernel modules (overlay, br_netfilter), sysctls and /dev/kmsg, which the unprivileged LXC module cannot give |
| Role / files | `ansible/roles/svc_k3s` (one role, node type per play; `tasks/escrow.yml` for the token escrow), templates in `apps/k3s/` (`config.yaml.j2`, `k3s.service.j2`, `keepalived.conf.j2`, `k3s-api-check.sh`), plays in `ansible/playbooks/site.yml` |
| Version | k3s `v1.36.5+k3s1` (stable release of 2026-09-30, one minor below the newest 1.37), binary from the official release, sha256 pinned |
| API endpoint | VIP `192.168.70.200:6443` (shr vip band .200-.239), keepalived VRRP on the control nodes. No service DNS name yet (`k8s` is not in services.yaml; a standards PR would add it) |
| Ports | see table below |
| Data | `/var/lib/rancher/k3s` (etcd in `server/db`, snapshots in `server/db/snapshots`), `/etc/rancher/k3s/config.yaml` and `/etc/rancher/k3s/agent-token` (0600) — **not backed up** (open item A-1) |
| Secrets | `shr/secret/apps/k3s/hm1-shr-k3sc01`, keys `server_token`, `agent_token` (names only): written once by the apply, create-only, never read by the pipeline or agents; Faris reads it. Nothing in Git |
| TLS | k3s internal CA (cluster-internal). API serving cert SANs: VIP, node IP, hostname. No ACME/OpenBao PKI yet |
| Upgrade | bump `svc_k3s_version` and `svc_k3s_sha256` in `ansible/roles/svc_k3s/defaults/main.yml`, PR, apply. Rolling, one node at a time, control first |
| Healthcheck | role end, per node: service active, `/readyz` (servers, via kubectl), node `Ready` per `kubectl get node`, and the VIP answers `/livez` (200 or 401) |

## Design
- **Embedded etcd HA, 3 servers + 2 agents.** The first control node in sorted order (`k3sc01`) runs `cluster-init: true`; `k3sc02/03` join it with `server:` and `token:`. Quorum tolerates the loss of one control node. Workers join through the VIP.
- **Servers are tainted** `CriticalAddonsOnly=true:NoExecute`: workloads land on the two workers; bundled addons (coredns, traefik, metrics-server, local-path) tolerate that taint and may run on control nodes.
- **Native binary and own systemd unit** (no `get.k3s.io` script): download with `get_url` and sha256 check, `/usr/local/bin/k3s`, units `k3s.service` (server) and `k3s-agent.service` (agent). `KillMode=process` so a k3s restart does not kill pods.
- **Kernel prerequisites in the role:** modules `overlay`, `br_netfilter` (`/etc/modules-load.d/k3s.conf` + modprobe), sysctls `net.ipv4.ip_forward`, `net.ipv6.conf.all.forwarding`, `net.bridge.bridge-nf-call-iptables/ip6tables` (`/etc/sysctl.d/90-k3s.conf`), swap off (now and fstab).
- **secrets-encryption** on (Kubernetes Secrets encrypted at rest in etcd). etcd snapshots every 12 h, keep 10, on the control nodes' local disk only.
- **Rollout order and safety:** `site.yml` runs control nodes with `serial: 1`, `order: sorted`, `any_errors_fatal`, then workers likewise. A node that does not become `Ready` stops the run before the next node is touched. k3s is restarted only if it was already running and its binary, config or unit changed; a first start is never followed by a restart.
- **Anonymous API access stays off.** On this k3s `/livez` answers 401 without credentials. Checks therefore treat 200 and 401 as "the API is serving": the keepalived script `/usr/local/bin/k3s-api-check` (curl, accepts 200/401/403, fails on no answer or 5xx) and the runner's VIP check (`status_code: [200, 401]`). Node-level readiness is checked with kubectl and the local kubeconfig.
- **Token escrow is the last play** (`k3s token escrow` in `site.yml`, `tasks/escrow.yml`): it runs after the control nodes and workers are complete, on the first control node only, so a failed OpenBao write (policy, token, namespace) fails only that play. It cannot leave the cluster half-configured and never restarts anything.

## Decisions
1. **Datastore (control nodes): `lvmt-nvme01`** (443 GB free; etcd is fsync-latency bound and k3s docs recommend SSD). Workers stay on the default `lvmt-sata01`. Set in the guest specs `hm1-shr-k3sc01..03` by the Infrastructure Engineer.
2. **API VIP: keepalived** (apt, distro version) rather than kube-vip: no in-cluster component or image to pin, the VIP exists before the API is needed by joiners, and it is one config file. VIP `192.168.70.200` is in the shr vip band (.200-.239); nothing else holds it. VRRP is unicast between the three control nodes, router id 70, priorities 150/140/130 (k3sc01 preferred), health script `k3s-api-check` (see Design). No VRRP auth (VRRPv3 has none; same L2 VLAN 20 only).
3. **Join tokens.** The cluster does not read them from OpenBao; the nodes get them from `k3sc01` during the run, and the tokens are escrowed in OpenBao afterwards:
   - **Server token**: generated by k3s on `k3sc01` (`/var/lib/rancher/k3s/server/token`); only `k3sc02/03` read it (runner memory, `no_log`) and write it to their own `config.yaml` (root, 0600).
   - **Agent token**: a distinct secret, generated once on `k3sc01` (`openssl rand -hex 32`, `/etc/rancher/k3s/agent-token`, root, 0600). All control nodes set it as `agent-token` in `config.yaml`; workers join with it (`token:`). It differs from the server token, so a worker credential cannot join a control node.
   - **Escrow** (Faris 2026-10): both tokens are written once to `shr/secret/apps/k3s/hm1-shr-k3sc01` (keys `server_token`, `agent_token`), same pattern as the AdGuard admin password. Create-only (`cas=0`), delegated to the runner with the apply run's short-lived `GUEST_SECRET_TOKEN`, `no_log` on every task, nothing in Git or logs. A marker `/etc/hm1/k3s-tokens-escrowed` (root, 0600, written only after the successful write) on `k3sc01` stops reruns from POSTing again. Create-only means rotation does not update OpenBao by itself: Faris deletes the old secret and the marker, then the next apply writes the new values (runbook, Tokens).
   - **Prerequisite (merge gate):** the ci-apply policy needs `path "+/secret/data/apps/k3s/*" { capabilities = ["create"] }` (Faris, `openbao/apply.sh`). Without it the escrow play fails with a 403 (cluster unaffected).
4. **Patching ring: all five nodes `ring1`** (Sev1/2 packages auto-apply). Every reboot still waits for Faris. Rule for reboots: one control node at a time; drain the node first, wait until `Ready` and etcd healthy before the next; never two control nodes at once (quorum), workers one at a time.

## Ports (inbound)
| Port | Proto | From | Purpose |
|---|---|---|---|
| 22 | tcp | CT 300 (192.168.70.20) | Ansible, pipeline tests |
| 6443 | tcp | all nodes, clients | Kubernetes API (nodes and clients use the VIP) |
| 2379-2380 | tcp | control nodes | etcd client / peer |
| 10250 | tcp | all nodes | kubelet |
| 8472 | udp | all nodes | flannel VXLAN |
| 112 | VRRP (IP proto) | control nodes | keepalived |
| 30000-32767 | tcp/udp | clients (if used) | NodePort range |

No host firewall is configured (Ubuntu default, ufw inactive). All five nodes share VLAN 20, so node-to-node traffic is not filtered by UniFi. Access to 6443 from other zones (kubectl from mgt/tru) and egress (container registries, github.com for the binary, HTTPS 443; DNS) is a network matter: `flows.yaml` is the Network Engineer's, nothing there yet for k3s. The escrow needs the runner (CT 300) to reach OpenBao 192.168.70.12:8200, same zone, as for AdGuard.

## Open points
- No backup of etcd snapshots or `/var/lib/rancher` (A-1); snapshots are local only. The token escrow does not replace an etcd backup.
- Optional standards PR: service name `k8s` in services.yaml for the VIP.
- Ingress/LoadBalancer exposure (traefik + servicelb defaults) and persistent storage (local-path only) are not designed yet.
- Kubeconfig: `/etc/rancher/k3s/k3s.yaml` on control nodes (root); for kubectl elsewhere copy it and set `server: https://192.168.70.200:6443`. It is a cluster-admin credential: handle as a secret, never commit.
