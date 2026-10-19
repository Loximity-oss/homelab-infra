# 4. Verification — is the guest working, is its app working?

## 4.1 Today (live 2026-10-19)

| Layer | Checks | When |
|---|---|---|
| L1 platform | running; Proxmox config = spec (cores, memory, disk, bridge/VLAN, tags) | apply + nightly 03:00 |
| L2 reachability | ping, SSH | same |
| L3 OS baseline | Goss per OS (`tests/os/*`) | same |
| L4 app | **DNS only** (`tests/apps/dns.goss.yaml.j2`): service, :53 tcp/udp, web UI, a real query, filters loaded, config drift | same |
| gone | destroyed guests really gone | apply |
| Monitoring | Prometheus: PVE host node, pve-exporter (guest up/down, disk), UniFi, WAN; 12 alerts → Alertmanager → OpenClaw incident | continuous |

**Gaps:** no k3s L4; services are only checked once a day (a dead AdGuard at 09:00 is found at 03:00 next day);
nothing tests from the client's side; no in-guest metrics.

## 4.2 Target: four parts

| Part | What | When | Repo |
|---|---|---|---|
| 1. Change gate | L1–L3 after infra apply; L4 after app deploy, per wave; stg auto-rollback | every change | infra + apps |
| 2. Nightly full | L1–L3 03:00, L4 + drift 03:15; one issue per failing host / app-host | daily | infra + apps |
| 3. **Continuous probes** | blackbox_exporter on CT 100, targets generated from specs + catalogue | every 60 s | openclaw config, apps data |
| 4. **Client's-eye** | each host of a `pair`/`cluster` deployment checks its partner(s) from inside the zone | L4 + probe | apps |

## 4.3 Continuous probes (new)

- **Exporter:** `prometheus-blackbox-exporter` on CT 100 next to Prometheus (openclaw `scripts/02-monitoring.sh`).
- **Targets:** a root job on CT 100 (every 10 min, like `openclaw-standards-sync`) clones infra + apps main read-only
  and writes `file_sd` JSON:

| Target set | From | Probe |
|---|---|---|
| every guest | infra specs | `icmp`, `tcp_connect :22` |
| every deployment host | catalogue `probes` | as declared (AdGuard: `dns_udp :53` query, `http_2xx :80/login.html`) |
| k3s | catalogue `probes` + deployment | `https://<vip>:6443/readyz` (TLS, status 200), per control node `:6443` |

- **Alerts** (openclaw `prometheus/`): `ProbeFailed` (2 m), `DNSQueryFailed` (2 m), `K3sAPIDown` (2 m),
  `GuestUnreachable` (icmp + ssh, 3 m). Labels carry `app`, `zone`, `host` so the incident run knows the owner.
- **Flows (oc-net):** CT 100 (mgt) → guests: SSH already allowed (`ssh_exec`); **new**: mgt → wifi/iot :53 and
  :80, mgt → shr :6443. To be added to `flows.yaml` and UniFi before step 4.3 goes live.

## 4.4 Client's-eye checks (new)

- In L4 and as a probe: each AdGuard host queries its **partner** in the same deployment (dns01 → dns02 and back) —
  traffic from inside the client zone, without a new probe guest.
- k3s: every node checks the VIP (`/readyz`) from inside shr.
- Declared in the entry (`cross_check`); `catalog.py check` requires it for `pair` and `cluster` patterns.

## 4.5 New L4 for k3s (`catalog/k3s/tests/l4.goss.yaml.j2`, read-only)

| Check | Pass when |
|---|---|
| service | `k3s` active (server on k3sc, agent on k3sw); `keepalived` active on control nodes |
| nodes | `kubectl get nodes` = 5, all `Ready` |
| etcd | `kubectl get --raw '/readyz?verbose'` reports `[+]etcd ok`; 3 nodes carry the `etcd` role label (k3s ships no `etcdctl`) |
| API | `https://<vip>:6443/readyz` = `ok` from every node |
| VIP | exactly one control node holds `<vip>` |
| version | running `k3s --version` = catalogue pin |
| system pods | `kube-system` pods Running/Completed, none CrashLoopBackOff |

## 4.6 Metrics (optional, later)

`node_exporter` on every guest via `base_common` (infra) + Prometheus `file_sd` from specs. It is the only part
that puts a new listener on every guest; only if Faris wants per-guest dashboards.

## 4.7 The loop

```
probe alert / failed test → incident run or issue → SRE triage → fix (PR, or hotfix then PR)
  → PM verify_guest(host) / verify_app(app, host) → green → close_issue with evidence
```

Faris is involved only for reboots and Class C.
