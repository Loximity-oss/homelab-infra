# mgt jumpbox: the one named exception to "no guest on vmbr0 / mgt"

**Guest:** `hm1-mgt-jump01`, VM, VMID 110, 192.168.67.10/28 (gateway 192.168.67.1), bridge vmbr0, Ubuntu 26.04 from template 9000,
2 vCPU / 2 GiB / 16 GB. **Requested by:** Faris, 2026-11-13 (Route B, pipeline-managed; openclaw #84, standards #57, #58).

**Why:** hairpinning is a design limitation of the network, and a transparent-L2 Sophos appliance (not yet onboarded) sits in the
path. A jumpbox in int/shr therefore cannot reliably reach management gear.

## Scope: exactly one guest
The hard limit stays in force for everything else. The exception is granted only when every layer names the same guest:

| Layer | Where | Matches on |
|---|---|---|
| Hard-limit record (Faris-only) | homelab-standards `protected.yaml` `exemptions.hm1-mgt-jump01` | vmid, ip (/32), bridge, type |
| Zone allowlist (Network Engineer) | `naming/zones.yaml` `mgt.guest_allow.hm1-mgt-jump01`, `guests: false` stays | vmid, ip, bridge, type |
| Lint | homelab-standards `lint/naming.py` | spec = allowlist = exemption = zone (vmid, ip, bridge, type); no extra_nics |
| Terraform | `terraform/modules/vm-guests` preconditions (`local.exempt`) | hostname + VMID + bridge in both records |
| Classifier | `policy/classify.py` (protected bridge on `network_device`, #236) | every change to this guest is Class C |
| Proxmox ACL (root) | `proxmox-acls.yaml` vmbr0 entry | Faris's decision; an ACL cannot be limited to one VMID |

Not covered: LXC on vmbr0 (lxc-guests unchanged), homelab-apps deployments into mgt (lint_apps unchanged), any second
hostname, VMID or IP in mgt.

## Open (see the PR)
- VLAN tagging of mgt on vmbr0 (net0 `tag=2` vs untagged). The module and verify.py L1 both assume `tag=2` today.
- `pool-mgt`, the OpenBao `mgt` namespace + PKI role, flows.yaml SSH from CT 300, the team prompt / tool checks (openclaw #84).

## Removing it
Delete the guest spec (destroy, Class C), then the `guest_allow` entry, then the protected.yaml exemption and the ACL entry.
