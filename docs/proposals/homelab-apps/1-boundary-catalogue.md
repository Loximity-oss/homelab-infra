# 1. Boundary, catalogue and deployments

## 1.1 Rule

> **Guest config** = anything every guest of an OS gets, or that makes the guest exist, reachable, patched and testable → `homelab-infra`.
> **App** = anything that makes a guest *do its job* → `homelab-apps`, as a catalogue entry plus deployments.

## 1.2 File moves (current → new)

| Today in `homelab-infra` | Goes to `homelab-apps` | Stays in infra |
|---|---|---|
| `ansible/roles/app_adguard/` (incl. `tasks/status.yml`) | `catalog/adguard/role/` | |
| `apps/adguard/*` (templates, `adguard-drift.py`) | `catalog/adguard/files/` | |
| `ansible/playbooks/group_vars/role_dns.yml` | shared defaults → `catalog/adguard/role/defaults/`; per-zone values → `deployments/{wifi,iot,stg}/adguard.yaml` `vars` | |
| `tests/apps/dns.goss.yaml.j2` (L4) | `catalog/adguard/tests/l4.goss.yaml.j2` | |
| `docs/apps/adguard*.md` | `catalog/adguard/docs/` | |
| `ansible/roles/svc_k3s/`, `apps/k3s/*`, `docs/apps/k3s*.md` | `catalog/k3s/{role,files,docs}/`; instance → `deployments/shr/k3s.yaml` (`api_vip`, `vrrp_router_id`, taints in `vars`) | |
| `ansible/roles/app_example/`, `apps/_example/`, `docs/apps/_template.md` | `catalog/_example/` (status `example`) | |
| site.yml plays "AdGuard DNS", "k3s control plane / workers / escrow" | `playbooks/deploy.yml` (1.7) | site.yml keeps "Base layer" + "Admin user" |
| `ansible/ansible.cfg` | apps gets its own copy (same settings) | infra keeps its own |
| `group_vars/all.yml` (timezone, base packages) | apps needs none of it (base layer already applied) | stays |
| | | `base_common`, `base_admin_user`, `patch_scan`, `patch_apply`, `verify` (L1–L3 part), `tests/os/*`, `ansible/keys/*` |
| | | `docs/apps/patching.md` → `docs/patching-os.md`; `hm1-dmz-app01-admin.md`, `vm-templates.md` → `docs/guests/` |
| openclaw skills `app-method`, `ansible-homelab`, `patching` | rewritten for the catalogue (5.3) | |
| role comments that point at `docs/apps/patching.md`, `changed.py` | updated or dropped with the move | |

Role file paths become `role_path`-relative (`{{ role_path }}/../../files/...`), never `playbook_dir/../../apps/...`.
`base_admin_user` (app01's admin user) is guest config: stays in infra.

## 1.3 Everything DevOps manages, and where

| Thing | Repo |
|---|---|
| Guests, templates, pools/tags (via specs), OS baseline, admin users, SSH keys, OS patching, PVE host helpers | infra |
| App definitions (catalogue) and instances (deployments) | **apps** |
| App secret values | OpenBao, paths unchanged (`<zone>/secret/apps/<app>[/<host>]`) |
| Naming, zones, role codes, flows, OpenBao/Proxmox ACL definitions | standards |
| Agent team, monitoring config (probes, alerts) | openclaw |

**Cross-repo reads (read-only, never writes):** apps reads infra (inventory, specs); infra reads the apps catalogue
for `health` and `reboot` (3.2, 3.4). If the apps clone is unavailable, infra falls back to the L3 basics and
`one_at_a_time`, and warns. Triggers stay one-way (infra → apps).

## 1.4 Layout

```
homelab-apps/
  README.md
  catalog/<app>/{app.yaml, role/, files/, tests/l4.goss.yaml.j2, docs/design.md, docs/runbook.md}
  catalog/_example/                     status example, compose-based
  deployments/<zone>/<app>.yaml
  playbooks/deploy.yml  ansible.cfg
  scripts/  policy/  .gitea/workflows/
  docs/orchestration.md  docs/patching.md  docs/verification.md  docs/adding-an-app.md  docs/catalog.md (generated)
```

## 1.5 Catalogue entry — `catalog/<app>/app.yaml` (AdGuard)

```yaml
name: adguard
owner: oc-cfg
catalog:
  status: stable                 # example | beta (stg only) | stable | deprecated
  summary: DNS filtering resolver for client zones
  role: dns                      # role code(s) in standards naming/roles.yaml
  pattern: pair                  # single | pair | cluster (+ count); stg may use `single` (1.6)
  os: [ubuntu]
  size: {type: lxc, cores: 1, memory_mb: 1024, disk_gb: 8}    # -> spec cores/memory_mb/disk_gb/type; os -> spec os
  ports: [53/udp, 53/tcp, 80/tcp]
  flows:                         # vocabulary 1.8
    - {from: self-zone, to: self, ports: [53/udp, 53/tcp]}     # clients of its own zone: same L2, no rule
    - {from: self, to: internet, ports: [53/udp, 53/tcp, 853/tcp, 443/tcp]}
    - {from: self, to: shr, ports: [8200/tcp]}                 # OpenBao
    - {from: hm1-shr-cicd01, to: self, ports: [22/tcp]}        # pipeline SSH
  secrets:
    - {path: "<zone>/secret/apps/adguard/<host>", keys: [username, password], by: role}   # generated at first deploy
version:
  pin: v0.107.79
  known:                         # every version a deployment may pin, with its checksum
    v0.107.79: c48f4a43000665484c5ec28177de11a004759b620dae8f77b2aabefc9ef3687f
  source: github:AdguardTeam/AdGuardHome
  bump_class: B
rollout:
  serial: 1
  order: sorted
  stop_on_failure: true
  health_between_hosts: true     # host n+1 starts only after host n passes `health`
  groups: []
health:                          # contract: plain shell on the guest, exit 0 = healthy, no secrets, hard timeout
  services: [AdGuardHome]
  command: "dig @127.0.0.1 probe.hm1.lox-internal.dev +time=3 +tries=1 +short | grep -qx 192.0.2.53"
  timeout_s: 20
reboot:
  policy: pair
  timeout_s: 600
probes:
  - {name: dns-query, module: dns_udp, port: 53, query: probe.hm1.lox-internal.dev, expect: 192.0.2.53}
  - {name: webui, module: http_2xx, port: 80, path: /login.html}
peer_check: {module: dns_udp, port: 53, query: probe.hm1.lox-internal.dev}
tests: tests/l4.goss.yaml.j2
drift: {in_l4: true}
```

`probe.hm1.lox-internal.dev → 192.0.2.53` is a fixed AdGuard rewrite added by the role (TEST-NET address, never routed),
so probes and `health` do not depend on WAN or upstream resolvers.

**k3s entry differences:** `role: [k3sc, k3sw]`, `pattern: cluster` with `count: {k3sc: 3, k3sw: 2}`, `size` per
role, `version.pin: v1.36.5+k3s1` with `known` from the release's `sha256sum-amd64.txt`, `bump_class: C`;
`rollout.groups`: (1) k3sc one at a time, first sorted host `--cluster-init`, `before: kubectl drain`,
`after: kubectl uncordon`, quorum check (3 `Ready` control nodes, `/readyz` etcd ok) before the next; (2) k3sw one at
a time with drain/uncordon; (3) escrow play. `health`: k3sc `k3s kubectl get --raw /readyz` = `ok`; k3sw
`systemctl is-active k3s-agent` + node `Ready` (checked from the first control node). `reboot.policy: drain`.
Secrets: `{path: "shr/secret/apps/k3s/<first control node>", keys: [server_token, agent_token], by: role}`.
Probe: `k3s-api` `https://<vip>:6443/readyz`.

**Versions:** semantic version of the part before `+`; build metadata (`+k3s1` → `+k3s2`) counts as a patch bump.
Unparsable = Class C.

## 1.6 Deployment — `deployments/<zone>/<app>.yaml`

```yaml
app: adguard
zone: wifi
hosts: [hm1-wifi-dns01, hm1-wifi-dns02]
version: catalog                 # or a pin from version.known
pin_until: null                  # required with a pin: date; the weekly check reminds after it
wave: 2                          # stg = 1 (enforced)
pattern: null                    # override only in stg (e.g. single)
vars: {upstreams: [...], filters: [...]}
host_vars: {}
```

| Check (`scripts/catalog.py check`, PR gate) | Result |
|---|---|
| host not (yet) in infra specs | **warning**; skipped at deploy until infra dispatches it |
| host exists with wrong role code or zone; host in two deployments | error |
| `beta` entry outside stg; new deployment of a `deprecated` entry | error |
| `pair`/`cluster` host count wrong | error (stg: may use `pattern: single`) |
| pin not in `version.known`; pin without `pin_until` | error |
| stg not wave 1, non-stg wave 1 | error |
| `stable` entry without a stg deployment | error unless `canary_waiver: "<reason, Faris, date>"` in the entry |
| secret path outside `<zone>/secret/apps/<app>` or `.../<app>/<host>` | error |
| entry missing `health`, `reboot`, `version`, `tests`, `probes`; `pair`/`cluster` without `peer_check` | error |

## 1.7 `playbooks/deploy.yml`

One play per deployment, built at run time: hosts = deployment hosts ∩ infra inventory; groups `role_<code>` are
derived from the infra inventory but limited to the deployment's hosts (so `groups['role_k3sc'] | sort | first`
keeps working); hostvars `zone`, `vmid`, `role` come from the inventory; deployment `vars` and `host_vars` are
loaded as extra play/host vars; `rollout` maps to `serial`, `order`, `any_errors_fatal`, and `groups` to ordered
sub-plays with `before`/`after` hooks. Env as today: `BAO_ZONE_MODE`, a short-lived `ci-apps` token for secret
creation.

## 1.8 Flow vocabulary (`catalog.flows`)

| `from` / `to` | Meaning |
|---|---|
| zone code (`shr`, `wifi`, …) | that zone from `naming/zones.yaml` |
| `self` | the deployment's hosts |
| `self-zone` | other hosts in the same zone/L2: never needs a rule |
| named host (`hm1-shr-cicd01`, `hm1-mgt-aiops01`) | that host's IP |
| `internet` | egress via the zone's router |

`plan_from_catalog` resolves every flow through the zone's `router`: `lox-coresw-01` → `flows.yaml` entry only;
`lox-gw-01` (wifi, iot) → `flows.yaml` entry **plus** a UDM policy proposal per host IP. Probe flows
(`hm1-mgt-aiops01 → self`: probe ports, plus icmp and 22/tcp in gateway-routed zones) are derived from `probes`,
never hand-listed.
