# 4. Verification — is the guest working, is its app working?

## 4.1 Today (live 2026-10-19)

| Layer | Checks | When |
|---|---|---|
| L1 platform | running; Proxmox config = spec (cores, memory, disk, bridge/VLAN, tags) | apply + nightly 03:00 |
| L2 reachability | ping, SSH | same |
| L3 OS baseline | Goss per OS (`tests/os/*`) | same |
| L4 app | **DNS only** (`tests/apps/dns.goss.yaml.j2`): service, :53 tcp/udp, web UI, a real query, filters, config drift | same (inside the 03:00 run) |
| gone | destroyed guests really gone | apply |
| Monitoring | Prometheus: PVE host node, pve-exporter (guest up/down, disk), UniFi, WAN; 12 alerts → Alertmanager → OpenClaw incident | continuous |

**Gaps:** no k3s L4; services are checked once a day; nothing checks from the client side; no in-guest metrics.

## 4.2 Target: four parts

| Part | What | When | Repo |
|---|---|---|---|
| 1. Change gate | L1–L3 after infra apply; L4 after app deploy, per wave; stg auto-rollback | every change | infra + apps |
| 2. Nightly full | L1–L3 03:00, L4 + drift 03:15; one issue per failing host / app-host | daily | infra + apps |
| 3. **Continuous probes** | blackbox_exporter on CT 100, targets generated from specs + catalogue | every 60 s | openclaw config, apps data |
| 4. **Peer checks** | each host of a `pair`/`cluster` deployment checks its partner(s) inside the zone | L4 + probe | apps |

At cutover (6, step 8) L4 leaves the 03:00 infra run and starts in the 03:15 apps run **in the same change set**,
so there is no night without L4 and no night with two.

## 4.3 Continuous probes (new)

- **Exporter:** `prometheus-blackbox-exporter` on CT 100 next to Prometheus (openclaw `scripts/02-monitoring.sh`).
- **Targets:** a job on CT 100 (every 10 min, user `openclaw`, Gitea read via the `oc-pm` token it already uses for
  `openclaw-standards-sync`) reads infra + apps main and writes `file_sd` JSON. It emits only guests that have a
  spec **and** are `running` (pve-exporter / read-only API); templates and scratch VMs never have specs.

| Target set | From | Probe |
|---|---|---|
| every guest | infra specs | `icmp`, `tcp_connect :22` |
| every deployment host | catalogue `probes` | as declared (AdGuard: `dns_udp :53` for `probe.hm1.lox-internal.dev`, `http_2xx :80`) |
| k3s | `probes` + deployment | `https://<vip>:6443/readyz` (200 `ok`) |

- **Alerts** (openclaw `prometheus/`), labels `app`, `zone`, `host`, `deployment`:

| Alert | Fires when |
|---|---|
| `GuestUnreachable` | icmp **and** ssh failing 3 m |
| `ProbeFailed` | an app probe failing 3 m |
| `DNSQueryFailed` | the DNS probe failing 3 m on **both** hosts of a pair (one host → `ProbeFailed` only) |
| `K3sAPIDown` | VIP `/readyz` failing 3 m |

- **Inhibit rules:** `GuestUnreachable` inhibits `ProbeFailed` for the same host; `DNSQueryFailed` inhibits the two
  `ProbeFailed`; so one outage = one incident. Approved reboots are silenced (3.4).
- **Flows (oc-net, verified against UniFi 2026-10-19):**

| Flow | Router | Needs |
|---|---|---|
| CT 100 → shr :6443 | coresw | `flows.yaml` entry only (mgt → shr already allowed) |
| CT 100 (192.168.67.3 only) → wifi/iot DNS host IPs: 53/udp, 80/tcp, icmp, 22/tcp | UDM (Internal → Untrusted is Block All) | `flows.yaml` entry + **UDM allow policy (Class C, Faris creates)** |

  The `file_sd` job emits targets in gateway-routed zones only after oc-net has confirmed the UDM policy exists
  (`state: implemented` in `flows.yaml`); until then those targets are skipped, so no false alerts.

## 4.4 Peer checks (new)

- Each AdGuard host queries its **partner** in the same deployment (dns01 → dns02 and back); each k3s node checks
  the VIP `/readyz`. Same-subnet L2 traffic: **no flows needed** (oc-net confirmed).
- They prove the partner works from inside the zone. They do **not** test the gateway-routed path clients from other
  zones use; a probe from another zone is added only if that path matters (none today).

## 4.5 k3s L4 (`catalog/k3s/tests/l4.goss.yaml.j2`, read-only), scoped per role

| Check | Where | Pass when |
|---|---|---|
| services | k3sc / k3sw | `k3s` / `k3s-agent` active; `keepalived` active on k3sc |
| version | every node | `k3s --version` = deployment version |
| nodes | k3sc only (has kubeconfig) | 5 nodes, all `Ready` |
| etcd | k3sc only | `k3s kubectl get --raw '/readyz?verbose'` has `[+]etcd ok`; 3 nodes with the `etcd` role label |
| API | every node | `https://<vip>:6443/readyz` = `ok` |
| system pods | k3sc only | `kube-system` pods Running/Completed, none CrashLoopBackOff |
| VIP holder | **deployment-level** (verify script, not per node) | exactly one control node holds `<vip>` |

## 4.6 Metrics (optional, later)

`node_exporter` on every guest via `base_common` + `file_sd` from specs; only if Faris wants per-guest dashboards.

## 4.7 The loop

```
probe alert / failed test → incident run or issue → SRE triage → fix (PR, or hotfix then PR)
  → PM verify_guest(host) / verify_app(app, host) → green → close_issue with evidence
```

Drill (6, step 10): stop AdGuard on the stg host → `ProbeFailed` must reach OpenClaw within **5 min**
(60 s interval + 3 m `for` + scrape jitter).
