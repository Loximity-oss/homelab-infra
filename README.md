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
ip: 192.168.70.10         # zone subnet; static band .10-.99 (lab: int .240-.254)
cores: 1
memory_mb: 1024
disk_gb: 8
owner: infra              # infra | sys | app | net | sre | faris
patch: ring1              # ring0 manual | ring1 auto Sev1/2 | ring2
# optional: datastore (default lvmt-nvme01), description, swap_mb, nesting, env
```

Open a PR titled like `feat(shr): add dns01`. Prefix `WIP: ` to plan without merging.
Network, VLAN, gateway, pool and tags come from the zone (homelab-standards `naming/zones.yaml`).

## Pipeline

| Workflow | When | What |
|---|---|---|
| `pr` (runner `host-plan`) | PR opened/updated (`pull_request_target`, scripts from `main`) | update branch if behind → standards lint, tf-safety, fmt, yamllint, gitleaks → plan changed stacks (read-only token) → classify → label, comment, gate |
| `apply` (runner `host-apply`) | merge to `main` | apply the PR's saved plan (fails if state moved) → Ansible on changed hosts → result comment |
| `drift` (runner `host-plan`) | nightly 02:30 MYT | plan every stack; drift opens a `drift: <stack>` issue |

| Class | Triggers | Gate |
|---|---|---|
| A | new guests, updates outside `shr`, docs | auto-merge |
| B | modules, Ansible roles/playbooks, imports, updates to `shr` guests | Reviewer (`oc-rev`) |
| C | any destroy/replace, protected VMIDs/bridge, `.gitea`, `policy`, `scripts`, `secrets`, `ansible/keys` | Faris |

## Layout

```
terraform/modules/lxc-guests/      guest module (bpg/proxmox)
terraform/stacks/hm1/<zone>/       one stack + state schema per zone (dmz, shr, int, lab)
  guests/<hostname>.yaml           guest specs
ansible/                           base_common role, site.yml; inventory = scripts/inventory.py (from specs)
ansible/keys/*.pub                 SSH keys injected into every guest
policy/classify.py                 Class A/B/C
scripts/                           CI (ci-pr, ci-apply, ci-drift), changed/inventory/gitea helpers, tf-safety
```

## Secrets

No secrets live in this repo. CI reads them from **OpenBao** (CT 303, `https://192.168.70.12:8200`) at job start.
PR and drift jobs run on runner `host-plan` with AppRole `ci-plan` (plan token, state DB, ci-merger token). Apply jobs run
on runner `host-apply` (separate Unix user, the only one that can read the `ci-apply` credentials) with AppRole `ci-apply`.
Tokens are revoked right after the fetch. New secrets go into OpenBao (`secret/hm1/<scope>/...`), never into Git.

Running Terraform or Ansible by hand bypasses plan/review/audit. Break-glass only.
