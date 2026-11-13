# mgt jumpbox: the one named exception to "no guest on vmbr0 / mgt"

**Guest:** `hm1-mgt-jump01`, VM, VMID 110, 192.168.67.10/28 (gateway 192.168.67.1), bridge vmbr0 untagged, Ubuntu 26.04 from
template 9000, 2 vCPU / 2 GiB / 16 GB. **Requested by:** Faris, 2026-11-13 (Route B, pipeline-managed; openclaw #84,
standards #57, #58). **NIC decision:** Faris, 2026-11-13 (homelab-infra #237, comment 3978): the pipeline creates and tests
the VM, the apply token gets NO right on vmbr0, root attaches net0 by hand once.

**Why:** hairpinning is a design limitation of the network, and a transparent-L2 Sophos appliance (not yet onboarded) sits in the
path. A jumpbox in int/shr therefore cannot reliably reach management gear.

## Scope: exactly one guest
The hard limit stays in force for everything else. The exception is granted only when every layer names the same guest:

| Layer | Where | Matches on |
|---|---|---|
| Hard-limit record (Faris-only) | homelab-standards `protected.yaml` `exemptions.hm1-mgt-jump01` | vmid, ip (/32), bridge, type |
| Zone allowlist (Network Engineer) | `naming/zones.yaml` `mgt.guest_allow.hm1-mgt-jump01`, `guests: false` stays | vmid, ip, bridge, type |
| Lint | homelab-standards `lint/naming.py` | spec = allowlist = exemption = zone (vmid, ip, bridge, type); no extra_nics |
| Terraform | `terraform/modules/vm-guests`: `local.exempt` routes the guest to resource `exempt` | hostname + VMID + bridge in both records |
| Classifier | `policy/classify.py` (protected bridge on `network_device`, #236) | changes once net0 is on vmbr0 (see below) |
| Proxmox ACL | `proxmox-acls.yaml`: unchanged, apply/plan/openclaw keep `NoAccess` on vmbr0 | nothing is granted |
| Tests | `scripts/verify.py` L1 `bridge+vlan` | exempt guest: bridge vmbr0, no tag, link up |

Not covered: LXC on vmbr0 (lxc-guests unchanged), homelab-apps deployments into mgt (lint_apps unchanged), any second
hostname, VMID or IP in mgt.

## How Terraform handles the NIC
The exempt guest has its own resource (`proxmox_virtual_environment_vm.exempt`, a lifecycle block cannot be conditional);
every other VM stays in `proxmox_virtual_environment_vm.guest`, unchanged. The `exempt` resource:
- creates net0 as a **placeholder**: the NIC template 9000 already has (bridge vmbr1, tag 60), with `link_down=1`. The
  apply token may use vmbr1 (DevOpsAdmin on `/`), so the create needs no right on vmbr0. With the link down and the VM
  stopped, the placeholder never carries a frame.
- leaves the VM **stopped** (`started = false`): its first boot, and so cloud-init's network setup (matched by net0's MAC),
  happens only on the real NIC.
- has `ignore_changes = [network_device, started]` (plus `pool_id`, `clone` as everywhere): Terraform never reverts root's
  net0 or stops the VM.

mgt is **untagged** on vmbr0: vmbr0 is not VLAN-aware (its config comment says "VLAN 2 untagged"; checked on the host
2026-11-13). net0 therefore carries no `tag`, and L1 expects none.

## Hand step (root, once per create)
After the apply that creates VM 110 (and again after any destroy + re-create):

```sh
qm config 110 | grep '^net0'        # e.g. virtio=BC:24:11:xx:xx:xx,bridge=vmbr1,firewall=0,link_down=1,tag=60
qm set 110 --net0 virtio=<MAC from above>,bridge=vmbr0,firewall=0   # keep the MAC; no tag, no link_down
qm start 110
```

Then the first verify: the PM runs `verify_guest(hm1-mgt-jump01)` (L1-L3). A green run closes the `verify:` issue; after that
the Configuration Engineer's role (XFCE + xrdp) can run.

**Until the hand step is done the tests of this guest fail, and that is expected:** the apply's own test run and the nightly
verify report L1 `running`, `bridge+vlan` and `guest agent IP` as failed, and Goss L2 (SSH) and L3 (OS baseline) fail
because the VM is stopped and has no network. The nightly verify opens `verify: hm1-mgt-jump01` until it is done.

The classifier marks a change Class C when a NIC is on a protected bridge in the plan. Once root has attached net0, the
refreshed state carries vmbr0 and every later change to this guest is C. The create itself is C through the new stack file
in the same PR.

## Open (see the PR)
- `pool-mgt`, the OpenBao `mgt` namespace + PKI role, flows.yaml SSH from CT 300, the team prompt / tool checks (openclaw #84).

## Removing it
Delete the guest spec (destroy, Class C), then the `guest_allow` entry, then the protected.yaml exemption.
