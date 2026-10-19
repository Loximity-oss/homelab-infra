# 1. Boundary, catalogue and deployments

## 1.1 Rule

> **Guest config** = anything every guest of an OS gets, or that makes the guest exist, reachable, patched and testable → `homelab-infra`.
> **App** = anything that makes a guest *do its job* → `homelab-apps`, as a catalogue entry plus deployments.

## 1.2 File moves (current → new)

| Today in `homelab-infra` | Goes to `homelab-apps` | Stays in infra |
|---|---|---|
| `ansible/roles/app_adguard/` | `catalog/adguard/role/` | |
| `apps/adguard/*` (templates, `adguard-drift.py`) | `catalog/adguard/files/` | |
| `ansible/playbooks/group_vars/role_dns.yml` | shared defaults → `catalog/adguard/role/defaults/`; per-zone values → `deployments/wifi/adguard.yaml`, `deployments/iot/adguard.yaml` | |
| `tests/apps/dns.goss.yaml.j2` (L4) | `catalog/adguard/tests/l4.goss.yaml.j2` | |
| `docs/apps/adguard*.md` | `catalog/adguard/docs/` | |
| `ansible/roles/svc_k3s/`, `apps/k3s/*`, `docs/apps/k3s*.md` | `catalog/k3s/{role,files,docs}/`; instance → `deployments/shr/k3s.yaml` | |
| `ansible/roles/app_example/`, `apps/_example/`, `docs/apps/_template.md` | `catalog/_example/` (status `example`) | |
| site.yml plays "AdGuard DNS", "k3s control plane / workers / escrow" | `playbooks/deploy.yml` (generic, driven by catalogue + deployment) | site.yml keeps "Base layer" + "Admin user" |
| | | `base_common`, `base_admin_user`, `patch_scan`, `patch_apply`, `verify` (L1–L3 part), `tests/os/*`, `ansible/keys/*` |
| | | `docs/apps/patching.md` → `docs/patching-os.md`; `hm1-dmz-app01-admin.md`, `vm-templates.md` → `docs/guests/` |
| `scripts/changed.py` rule "apps/ → configure all hosts" | removed at cutover | |

`base_admin_user` (app01's admin user) is guest config: stays in infra.

## 1.3 Everything DevOps manages, and where

| Thing | Repo |
|---|---|
| Guests, templates, pools/tags (via specs), OS baseline, admin users, SSH keys, OS patching, PVE host helpers | infra |
| App definitions (catalogue) and instances (deployments): AdGuard, k3s, future host apps | **apps** |
| App secret values | OpenBao, paths unchanged (`<zone>/secret/apps/<app>/...`) |
| Naming, zones, role codes, flows, OpenBao/Proxmox ACL definitions | standards |
| Agent team, monitoring config | openclaw |

## 1.4 Layout

```
homelab-apps/
  README.md                       how to add, deploy, test, patch an app
  catalog/<app>/
    app.yaml                      the catalogue entry (1.5)
    role/  files/                 Ansible role + config templates/scripts it renders
    tests/l4.goss.yaml.j2         L4 app checks (Class C)
    docs/design.md  docs/runbook.md
  catalog/_example/               copy-me entry (status example), compose-based
  deployments/<zone>/<app>.yaml   instances (1.6)
  playbooks/deploy.yml            generic: one deployment = one play with the entry's rollout rules
  scripts/  policy/  .gitea/workflows/
  docs/orchestration.md  docs/patching.md  docs/verification.md  docs/adding-an-app.md  docs/catalog.md (generated index)
```

## 1.5 Catalogue entry — `catalog/<app>/app.yaml` (AdGuard)

```yaml
name: adguard                        # = folder name
owner: oc-cfg
catalog:
  status: stable                     # example | beta (stg only) | stable (any guest zone) | deprecated (no new deployments)
  summary: DNS filtering resolver for client zones
  role: dns                          # role code every host of a deployment must have (standards naming/roles.yaml)
  pattern: pair                      # single | pair | cluster   (+ count for cluster)
  os: [ubuntu]                       # templates it is tested on
  size: {type: lxc, cores: 1, memory_mb: 1024, disk_gb: 8}   # baseline for new guests
  ports: [53/udp, 53/tcp, 80/tcp]
  flows:                             # what the network engineer must allow (standards flows.yaml)
    - {from: zone-clients, to: self, ports: [53/udp, 53/tcp]}
    - {from: shr-cicd, to: self, ports: [22/tcp]}          # pipeline SSH (exists for every guest)
  secrets: [admin_password]          # created at first deploy, create-only, at <zone>/secret/apps/adguard/<host>
version:
  pin: v0.107.79
  sha256: c48f4a43000665484c5ec28177de11a004759b620dae8f77b2aabefc9ef3687f
  source: github:AdguardTeam/AdGuardHome   # weekly version check
  bump_class: B                      # patch/minor; major is always C
rollout:
  serial: 1
  order: sorted
  stop_on_failure: true
  groups: []                         # ordered sub-plays (k3s: servers, then agents, then escrow)
health:                              # per-host gate for OS patching and reboots
  services: [AdGuardHome]
  command: "goss-lite: the app-dns-query check of tests/l4"   # exact command fixed when the entry is moved (step 5)
  timeout_s: 60
reboot:
  policy: pair                       # one_at_a_time | pair | drain
  before: []
  after: []
probes:                              # continuous, from Prometheus blackbox on CT 100 (4.3)
  - {name: dns-query, module: dns_udp, port: 53, query: gitea.com}
  - {name: webui, module: http_2xx, port: 80, path: /login.html}
cross_check: {module: dns_udp, port: 53, query: gitea.com}   # each host queries its partner (4.4)
tests: tests/l4.goss.yaml.j2
drift: {in_l4: true}
```

**k3s entry differences:** `role: [k3sc, k3sw]` (two roles), `pattern: cluster` with `count: {k3sc: 3, k3sw: 2}`,
`size` per role, `rollout.groups` (servers one at a time with `--cluster-init` on the first sorted control node, then
agents, then the escrow play), `reboot.policy: drain` with `before: kubectl drain …` and `after: kubectl uncordon …`,
`version.bump_class: C`, probes: `k3s-api` (`https://<vip>:6443/readyz`, `tcp_tls`), `catalog.status: stable`.

## 1.6 Deployment — `deployments/<zone>/<app>.yaml`

```yaml
app: adguard
zone: wifi                           # = folder; checked
hosts: [hm1-wifi-dns01, hm1-wifi-dns02]
version: catalog                     # or a pin, e.g. v0.107.79, to hold this deployment back
wave: 2                              # stg deployments are wave 1 (enforced)
vars:                                # instance config (was group_vars role_dns.yml + zone overrides)
  upstreams: [...]
  filters: [...]
host_vars: {}                        # rare per-host overrides
```

Checks (`scripts/catalog.py check`, PR gate):

| Check | Fails when |
|---|---|
| entry | name ≠ folder; role code not in standards `roles.yaml`; missing `health`, `reboot`, `version`, `tests`, `probes` |
| status | `beta` entry deployed outside `stg`; `deprecated` entry gets a new deployment |
| deployment hosts | host not in infra guest specs (main), wrong role code, wrong zone, or the host already belongs to another deployment |
| pattern | `pair` deployment without exactly 2 hosts; `cluster` count differs (warning in stg, error elsewhere) |
| version | pin not a valid version of the entry; `sha256` missing |
| wave | stg deployment not wave 1; non-stg deployment wave 1 |
| secrets | path outside `<zone>/secret/apps/<app>/` |
