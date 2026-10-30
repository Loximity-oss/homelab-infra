# Lab VMs without an OS (os: none) and isolated lab bridges

First use: `hm1-stg-net01`, a GNS3 VM (Faris, 2026-10-31). Rules: homelab-standards `lint/naming.py`; module:
`terraform/modules/vm-guests`; tests: `scripts/verify.py`.

## Spec keys (type: vm only)

| Key | Meaning |
|---|---|
| `os: none` | Blank `scsi0` of `disk_gb` (raw on lvm-thin), no template clone, no cloud-init, guest agent off, VGA console. Must come with `tests: l1-only` |
| `tests: l1-only` | Per-guest test exemption. Lint allows it **only** with `os: none`; every guest with an OS keeps L1-L3 |
| `extra_nics: [vmbrNN, ...]` | net1.. in this order, no VLAN tag. Each bridge must be in the zone's `lab_bridges` (homelab-standards `naming/zones.yaml`, Network Engineer); never vmbr0 |
| `cpu_type` | `x86-64-v2-AES` (default) or `host` (nested virtualisation, e.g. GNS3 QEMU nodes) |

`ip` stays required: for `os: none` it reserves the zone static IP that is configured **inside** the guest by hand.

## What the pipeline does with an `os: none` guest

- Terraform creates and starts it (empty disk: it sits at "no bootable device" until an image is installed).
- Not in the Ansible inventory (`scripts/inventory.py`): no base layer, no patch scan, no admin password.
- Tests: L1 runs in full (exists, type, running, name, pool, tags, cores, memory, net0 bridge+VLAN, q35, disk, and per
  extra NIC `netN <bridge>` + `<bridge> isolated`). `guest agent IP`, `ip`, L2 and L3 are reported as ⚠️ skip with the
  reason, never as ✅.
- Installing the lab image (ISO / imported disk) by hand changes the VM config (cdrom, boot order, maybe disks):
  that shows up as drift. Codify the install method in the spec before doing it.

## Lab bridges are host network config (root, hand-made)

PVE bridges are **not** managed by any code in homelab-infra or homelab-standards. `vmbr0` (mgt, protected) and
`vmbr1` (VLAN trunk for the guest zones) were built by hand on `hm1-mgt-pve01`; the repos only reference them
(`naming/zones.yaml` `bridge`, `protected.yaml` `bridges`, `proxmox-acls.yaml` SDN paths, the module preconditions,
`verify.py` bridge+vlan, the pve-feature-broker vmbr0 refusal). The pipeline never runs as root and never edits
`/etc/network/interfaces`.

For `hm1-stg-net01`, root adds nine isolated bridges (no ports, no IP, no VLAN awareness), appended to
`/etc/network/interfaces` so the PVE GUI shows them:

```
auto vmbr10
iface vmbr10 inet manual
        bridge-ports none
        bridge-stp off
        bridge-fd 0
#GNS3 lab segment 1 (hm1-stg-net01 net1), isolated
```

... the same for `vmbr11` to `vmbr18` (segments 2-9, net2-net9). Then `ifreload -a` (ifupdown2; vmbr0/vmbr1 are
unchanged, so not touched). Check: `ip -d link show vmbr10` (no `master` port), `bridge link | grep vmbr1[0-8]` (empty
before the VM starts).

Also on the host, for `cpu_type: host` to be useful: nested virtualisation on (`cat /sys/module/kvm_intel/parameters/nested`
or `kvm_amd` = `Y`/`1`).

Nothing else is needed on the host: the apply token's DevOpsAdmin on `/` already includes SDN.Use for the new bridges
(only `vmbr0` is NoAccess, `proxmox-acls.yaml`), the plan token's Sys.Audit reads `/nodes/<node>/network` for the L1
isolation check, and the pve-feature-broker ignores VMs without `ipconfig0`.

Order: bridges on the host -> Network Engineer lists them as `lab_bridges` of `stg` in `naming/zones.yaml` -> guest PR.
