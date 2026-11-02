# homelab-infra

This repo holds the guests, the base OS configuration, and the pipeline for site `hm1`. Git is the source of truth. It does not hold applications. The `homelab-apps` repo holds them.

## Status

- State: live.
- Checked: 2026-11-02.
- Used by: the runners on `hm1-shr-cicd01`, the Proxmox host `hm1-mgt-pve01`, and the OpenClaw agents.
- A merge to `main` applies the saved plan. The apply step then configures the changed guests.

## Place in the system

| Repo | Owns |
|---|---|
| **`homelab-infra`** (this repo) | Guests, base OS, pipeline, patching |
| `homelab-apps` | Application catalogue and deployments |
| `homelab-standards` | Naming rules, hard limits, OpenBao policies |
| `openclaw` | The AIOps agent team |

- The PR check reads the rules from `homelab-standards`.
- After an apply that configures guests, the pipeline starts the `deploy` workflow of `homelab-apps`.

## Layout

```
terraform/modules/{lxc-guests,vm-guests}/   guest modules (bpg/proxmox)
terraform/stacks/hm1/<zone>/                one stack and one state schema for each zone
  guests/<hostname>.yaml                    one spec for each guest
terraform/templates/hm1/                    VM template specs
ansible/playbooks/, ansible/roles/          base OS, verify, and patch plays
ansible/keys/*.pub                          SSH public keys for every guest
policy/classify.py                          computes the change class
scripts/                                    pipeline scripts and helpers
tests/os/                                   Goss tests for the OS baseline
host/                                       root helpers on the Proxmox host and on the CI guest
patches/hm1/                                monthly patch bundles
docs/                                       guides and design documents
.gitea/workflows/                           the pipeline
```

The zones are `dmz`, `shr`, `int`, `stg`, `wifi`, and `iot`.

## Change process

- Every change is a PR. Nobody pushes to `main`.
- Branch: `<type>/<issue#>-<slug>`. Title: `type(scope): text`. Start the title with `WIP: ` for a plan without a merge.
- The `pr` workflow runs these checks:
  1. the naming lint from `homelab-standards`
  2. `tf-safety`
  3. `terraform fmt`
  4. `yamllint`
  5. `gitleaks`
  6. a plan of each changed stack
- `policy/classify.py` computes the class from the plans and the changed paths. A person cannot set the class.

| Class | Change | Approval |
|---|---|---|
| A | Everything not listed for B or C. Examples: new guests, updates outside `shr`, docs | None. The merge broker merges |
| B | Modules, Ansible roles and playbooks, imports, updates to existing `shr` guests, a minor template refresh | `oc-rev` approves |
| C | A destroy or replace. A host-level resource (pool, cluster option, storage, network, firewall, ACL). A protected VMID or bridge. A template VMID | Faris approves |
| C | The paths `host/`, `.gitea/`, `policy/`, `scripts/`, `tests/`, `patches/`, `ansible/keys/`, the UniFi stacks, the stack `.tf` files, and template specs | Faris approves |

- Only the merge broker or Faris merges a PR.
- After the merge, the `apply` workflow applies the saved plan of the PR. It fails if the state changed since the plan.

## Write access

- Branch `main`: nobody can push. Only `ci-merger` and `faris` can merge. Checked live on 2026-11-02.
- Other branches: `ci-merger`, `faris`, `oc-cfg`, `oc-infra`, and `oc-net` can push.
- The agent tools limit the files that each agent can write. The limits are in `mcp/devops_server.py` in the `openclaw` repo.

| Agent | Can write |
|---|---|
| `oc-infra` | `policy/`, `scripts/`, `terraform/`, `docs/`, `README.md`, `ansible/keys/`. Not the UniFi code, `docs/apps/`, or `docs/network/` |
| `oc-cfg` | `ansible/`, `apps/`, `docs/apps/`. Not `ansible/keys/` |
| `oc-net` | The UniFi stacks and modules, `docs/network/` |

No agent can write `.gitea/`. The class rules also apply to a PR from an agent.

## Key concepts

**Guest spec.** One file in `terraform/stacks/hm1/<zone>/guests/<hostname>.yaml` defines one guest.

```yaml
hostname: hm1-shr-dns01   # <site>-<zone>-<role><nn>, at most 15 characters, same as the file name
vmid: 315                 # inside the VMID block of the zone
type: lxc                 # lxc or vm
role: dns                 # from naming/roles.yaml
os: ubuntu2604
ip: 192.168.70.10         # inside the static band .10-.99 of the zone
cores: 1
memory_mb: 1024
disk_gb: 8
owner: infra              # infra, cfg, net, sre, or faris
patch: ring1              # ring0, ring1, or ring2
```

- Optional keys: `datastore`, `description`, `env`, `swap_mb`, `nesting`, and `keyctl`. `swap_mb`, `nesting`, and `keyctl` are for LXC only.
- An LXC guest uses the datastore `lvmt-nvme01` by default.
- A VM clones a protected template. The default size is 2 vCPU, 2048 MB RAM, and 16 GB disk on `lvmt-sata01`.
- The zone gives the VLAN, the gateway, the pool, and the tags. The source is `naming/zones.yaml` in `homelab-standards`.
- A VM with `os: none` is a lab VM. See `docs/lab-vms.md`.

**Templates.** The `template` workflow builds the VM templates (VMIDs 9000 to 9099) from the specs in `terraform/templates/hm1/`. A guest stack never builds a template.

**Patch ring.** `ring0` is manual. `ring1` gets automatic patches for severity 1 and 2. The pipeline never reboots a guest by itself.

**Snapshot.** The pipeline takes a snapshot before a risky step. The names are `pre-apply_`, `pre-patch_`, and `pre-reboot_`.

## Local checks

Run these checks on your branch before the PR:

```
terraform fmt -check -recursive terraform
yamllint -d relaxed terraform/stacks ansible .gitea
gitleaks dir . --no-banner --redact
bash scripts/tf-safety.sh .
python3 <standards checkout>/lint/naming.py --infra . --branch <branch> --title "<title>"
```

Each check prints the problems it finds. The `pr` workflow runs the same checks.

Caution: do not run Terraform or Ansible by hand against the live site. A manual run bypasses the plan, the review, and the audit. Use it only to recover from a failure.

## Operations

| Workflow | Runner | When (MYT) | What |
|---|---|---|---|
| `pr` | `host-plan` | Each PR event | Checks, plan, class, gate |
| `apply` | `host-apply` | Merge to `main` | Saved plan, Ansible on changed guests, L1 to L3 checks |
| `drift` | `host-plan` | 02:30 every day | Plan of every stack. A difference opens a `drift: <stack>` issue |
| `verify` | `host-apply` | 03:00 every day | L1 to L3 checks. A failure opens a `verify: <host>` issue |
| `template` | `host-template` | A merge that changes `terraform/templates/**`. Monday 04:00 | Build or refresh the VM templates |
| `patch` | `host-apply` | 09:40, 15:40, 21:40, 03:40 | Scan. Patch severity 1 and 2 on `ring1` guests |
| `patch-bundle` | `host-apply` | The 25th, 09:00 | Open a PR with the severity 3 and 4 bundle |
| `patch-monthly` | `host-apply` | Saturday 02:00 | On the last Saturday only: apply the bundle that Faris approved |
| `reboot` | `host-apply` | Started by an OpenClaw tool | Reboot one guest after Faris approves in a `patch: reboot <host>` issue |

- Host locks in `/var/lib/devops/locks` keep the runs apart.
- The runner users and AppRoles are: `host-plan` with `ci-plan`, `host-apply` with `ci-apply`, and `host-template` with `ci-template`.
- The patch results are in `/var/lib/devops/patch/` on the runner for 35 days.
- The merge broker runs as a timer on `hm1-shr-cicd01`. Read its log with `journalctl -u merge-broker`.
- The Proxmox host helper writes its log to `journalctl -t pve-feature-broker`.

## Secrets and safety

This repo holds no secret. The jobs read the secrets from OpenBao at the start of the job, then revoke the token. New secrets go in OpenBao under `secret/hm1/<scope>/`.

- Caution: the pipeline never changes a guest listed in `protected.yaml`, or the bridge `vmbr0`. The pipeline token cannot see these guests.
- Caution: the `apply` runner is the only runner that reads the `ci-apply` credentials. It runs as its own Unix user.
- Caution: root installs the files in `host/`. A change there is always Class C.

## Documents

| Document | Topic |
|---|---|
| `docs/templates.md` | The life cycle of the VM templates |
| `docs/lab-vms.md` | Lab VMs without an OS |
| `docs/patching-pipeline.md` | Patching, pipeline side |
| `docs/apps/` | Notes per application, from before the move to `homelab-apps` |
| `docs/proposals/homelab-apps/` | The design of `homelab-apps` |
| `host/README.md` | The root helpers on the Proxmox host |
| `tests/README.md` | The guest tests, L1 to L3 |

## Known gaps

- `docs/apps/` still holds notes from before the move to `homelab-apps`. Move the current notes to that repo.
- The template builder cannot set registered tags. After each build, root sets the tags with `qm set <vmid> --tags`.
