# homelab-infra

Git is the source of truth for site `hm1`. Every change is a pull request; the pipeline lints, plans,
classifies and gates it, then applies the saved plan after merge and configures hosts with Ansible.
Naming rules and hard limits live in **homelab-standards** (Faris-only) and are enforced on every PR.

## Add a guest (LXC)

Create `terraform/stacks/hm1/<zone>/guests/<hostname>.yaml` on a branch `feat/<issue#>-<slug>`:

```yaml
hostname: hm1-shr-dns01   # <site>-<zone>-<role><nn>, ≤15 chars, = file name
vmid: 301                 # inside the zone block (shr = 300-399); 100/300 protected
type: lxc
role: dns                 # naming/roles.yaml
os: ubuntu2604
ip: 192.168.70.10         # zone subnet; static band .10-.99
cores: 1
memory_mb: 1024
disk_gb: 8
owner: infra              # infra | cfg | net | sre | faris
patch: ring1              # ring0 manual | ring1 auto Sev1/2 | ring2
# optional: datastore (default lvmt-nvme01), description, swap_mb, nesting, env, keyctl
```

**VMs** (`type: vm`, module `vm-guests`, design-v7 decision 32): cloned in full from the protected template for `os`
(`ubuntu2604` → 9000), cloud-init sets the hostname, static IP, root + `ansible/keys/*`. Sizing keys are optional:
the baseline is **2 vCPU / 2048 MB / 16 GB on `lvmt-sata01`** (D6); set `cores`, `memory_mb`, `disk_gb`, `datastore`
for the workload (Configuration Engineer). `disk_gb` ≥ 8 (template). `swap_mb`, `nesting`, `keyctl` are LXC-only.

```yaml
hostname: hm1-stg-tst02
vmid: 813
type: vm
role: tst
os: ubuntu2604
ip: 192.168.76.12
owner: infra
patch: ring1
```

Open a PR titled like `feat(shr): add dns01`. Prefix `WIP: ` to plan without merging.
Network, VLAN, gateway, pool and tags come from the zone (homelab-standards `naming/zones.yaml`).

**VM templates** (9000-9099) are built by the `template` workflow from specs in `terraform/templates/hm1/`, never by
guest stacks: see `docs/templates.md`.

## Pipeline

| Workflow | When | What |
|---|---|---|
| `pr` (runner `host-plan`) | PR opened/updated (`pull_request_target`, scripts from `main`) | update branch if behind → standards lint, tf-safety, fmt, yamllint, gitleaks, template specs → plan changed stacks (read-only token) → classify → label, comment, gate |
| `apply` (runner `host-apply`) | merge to `main` | apply the PR's saved plan (fails if state moved) → Ansible on changed hosts → result comment |
| `drift` (runner `host-plan`) | nightly 02:30 MYT | plan every stack; drift opens a `drift: <stack>` issue |
| `reboot` (runner `host-apply`) | dispatched by OpenClaw `reboot_guest` after Faris's `reboot` comment | snapshot, drain/reboot one guest, verify (k3s control nodes one at a time) |
| `template` (runner `host-template`) | merge touching `terraform/templates/**`; weekly refresh check | build/refresh VM templates with the builder token `svc-tpl-build` (the apply token stays clone-only) |

| Class | Triggers | Gate |
|---|---|---|
| A | new guests, updates outside `shr`, docs | auto-merge |
| B | modules, Ansible roles/playbooks, imports, updates to `shr` guests, minor template refresh (url/sha256/version of an existing spec) | Reviewer (`oc-rev`) |
| C | any destroy/replace, **any host-level resource** (pools, cluster options, storage, network, SDN, firewall, ACLs), protected VMIDs/bridge, `.gitea`, `policy`, `scripts`, `ansible/keys`, template specs (new, retired, settings) | Faris |

## Layout

```
terraform/modules/lxc-guests/      guest module (bpg/proxmox)
terraform/stacks/hm1/<zone>/       one stack + state schema per zone (dmz, shr, int, stg, wifi, iot)
  guests/<hostname>.yaml           guest specs
terraform/templates/hm1/           VM template specs (template workflow; docs/templates.md)
ansible/                           base_common role, site.yml; inventory = scripts/inventory.py (from specs)
ansible/keys/*.pub                 SSH keys injected into every guest
policy/classify.py                 Class A/B/C
scripts/                           CI (ci-pr, ci-apply, ci-drift, ci-template), changed/inventory/gitea helpers, tf-safety
```

## Applications

AdGuard Home and k3s are **not** configured from this repo since the homelab-apps cutover (infra #127, 2026-10-22).
They live in **homelab-apps** (`catalog/<app>/`, `deployments/<zone>/<app>.yaml`, own runner `host-apps`, AppRole `ci-apps`).
This repo creates the guests, runs `base_common` and the `verify` L1–L3 checks; after an `apply` that configures hosts
it dispatches the homelab-apps `deploy` workflow (`APPS_DISPATCH` in `apply.yml`). Per-app notes: `docs/apps/`.
Design and decisions: `docs/proposals/homelab-apps/`.

## Secrets

No secrets live in this repo. CI reads them from **OpenBao** (CT 303, `https://192.168.70.12:8200`) at job start.
PR and drift jobs run on runner `host-plan` with AppRole `ci-plan` (plan token, state DB, ci-merger token). Apply jobs run
on runner `host-apply` (separate Unix user, the only one that can read the `ci-apply` credentials) with AppRole `ci-apply`.
Template jobs run on runner `host-template` (user `runner-template`) with AppRole `ci-template` (builder token only).
Tokens are revoked right after the fetch. New secrets go into OpenBao (`secret/hm1/<scope>/...`), never into Git.

Running Terraform or Ansible by hand bypasses plan/review/audit. Break-glass only.
