# VM templates: minimum requirements

Templates live in VMID 9000-9199 (protected.yaml `templates`) and are clone-only for Terraform guest applies.
Linux templates in 9000-9099 are built and refreshed by the `template` workflow from the reviewed specs in
`terraform/templates/hm1/` (lifecycle, gates and build steps: `docs/templates.md`). Windows 9100-9199 stays manual.
This note only holds the in-guest requirements. `terraform/modules/vm-guests/main.tf`
clones a template and sets, per guest: q35, virtio-scsi-single, serial0 socket + `vga serial0`, QEMU agent enabled
(waits up to 10 min), cloud-init drive with static IP, DNS, and `user_account root` + the pipeline SSH keys
(`ansible/keys`). Ansible then connects as root over SSH and needs python3.

| VMID | os tag | Template | Status |
|---|---|---|---|
| 9000 | ubuntu2604 | Ubuntu 26.04 cloud image | exists, verified 2026-10-08 |
| 9001 | arch | Arch Linux cloud image | to build |
| 9002 | fedora44 | Fedora 44 Cloud Base | to build |
| 9003 | nixos | NixOS, self-built | to build |

## Common requirements (every template)

- The disk must grow on clone (cloud-init `growpart`/`resizefs`, or the OS equivalent) so `disk_gb` takes effect.
  The template disk size is the lower bound (`min_disk_gb`).
- Packages: `cloud-init`, `qemu-guest-agent`, an SSH server, `python3` (Ansible). `sudo` is not needed (root login).
- Enabled at boot: `sshd`, `qemu-guest-agent`, the cloud-init services (local, init/main, config, final), and
  cloud-init network configuration (static IP from the Proxmox cloud-init drive; DHCP must not be required).
- Root SSH by key: `user_account root` only works if root login is allowed. Set `disable_root: false` in cloud-init
  config and `PermitRootLogin prohibit-password` in sshd. Password login off.
- Serial console on ttyS0 (`console=ttyS0,115200 console=tty0`) because the VM has `vga serial0`.
- Hygiene before `qm template`: no pre-baked SSH host keys, `cloud-init clean --logs`, empty `/etc/machine-id`,
  no static IP/hostname, no secrets or user keys baked in.
- Verify on a throwaway clone (as done for 9000): agent reports the IP, `ssh root@ip` works with the CI key, `python3 -V`.

Build sketch on the PVE host (Faris, root):

```
qm create 900X --name tpl-<os> --machine q35 --memory 2048 --cores 2 --net0 virtio,bridge=<stg bridge> \
  --scsihw virtio-scsi-single --agent enabled=1 --serial0 socket --vga serial0 --ostype l26
qm importdisk 900X <image>.qcow2 <datastore>
qm set 900X --scsi0 <datastore>:vm-900X-disk-0,discard=on,iothread=1,ssd=1 --boot order=scsi0 --ide2 <datastore>:cloudinit
qm template 900X
```

Then add the per-template `DevOpsTemplateUse` ACL for the apply token (checked by `scripts/template-guard.py`).
Image locations below are starting points; confirm the current path and checksum on the vendor site first.

## Arch Linux (9001, os `arch`)

- Source: official `Arch-Linux-x86_64-cloudimg.qcow2` from `geo.mirror.pkgbuild.com/images/latest/` (verify checksum).
- Should ship cloud-init, qemu-guest-agent, openssh and python (verify with `pacman -Q`). Enable `sshd`,
  `qemu-guest-agent`, `cloud-init-local`, `cloud-init-main`, `cloud-init-network`, `cloud-config`, `cloud-final`,
  `systemd-networkd`, `systemd-resolved`.
- Default user `arch`; root login disabled by default: add `disable_root: false` in `/etc/cloud/cloud.cfg.d/`.
- Rolling release. Package manager: `pacman`.

## Fedora 44 (9002, os `fedora44`)

- Source: Fedora Cloud Base qcow2 under `download.fedoraproject.org/pub/fedora/linux/releases/44/Cloud/x86_64/images/`
  (verify against the CHECKSUM file).
- Ships cloud-init, qemu-guest-agent, openssh-server, python3. Ensure `qemu-guest-agent`, `sshd`, `cloud-init-local`,
  `cloud-init`, `cloud-config`, `cloud-final` are enabled; NetworkManager is the backend (cloud-init drives it).
- Default user `fedora`; set `disable_root: false`. SELinux stays enforcing. Package manager: `dnf`.

## NixOS (9003, os `nixos`)

No stock cloud-init qcow2 exists. Build once (install from the minimal ISO with this config, or `nixos-generators
-f qcow`), then templatise. Minimal `configuration.nix` fragment:

```nix
{ config, pkgs, lib, ... }:
{
  boot.loader.grub = { enable = true; device = "/dev/sda"; };   # or systemd-boot for UEFI
  boot.kernelParams = [ "console=ttyS0,115200" "console=tty0" ];
  boot.growPartition = true;
  fileSystems."/" = { device = "/dev/disk/by-label/nixos"; fsType = "ext4"; autoResize = true; };

  services.cloud-init = {
    enable = true;
    network.enable = true;            # static IP from the Proxmox cloud-init drive
    settings.disable_root = false;
  };
  services.qemuGuest.enable = true;   # qemu-guest-agent
  services.openssh = {
    enable = true;
    settings = { PermitRootLogin = "prohibit-password"; PasswordAuthentication = false; };
  };
  environment.systemPackages = with pkgs; [ python3 ];
  networking.useDHCP = false;         # cloud-init configures the interface
  system.stateVersion = "25.11";      # set to the release actually installed
}
```

Notes: `/etc` is declarative, so durable changes (packages, users, services) go through `configuration.nix`, not
imperative Ansible tasks. Option names (`services.cloud-init.settings`, `network.enable`) should be checked against
the installed release. A dedicated `os_nixos` role (rendered `configuration.nix` + `nixos-rebuild switch`) is a follow-up.

## Follow-ups before any guest of these OSes can deploy

`ansible/roles/base_common/tasks/main.yml` uses `ansible.builtin.apt` unconditionally, so it fails on all three. It
was deliberately not changed: a silent `when: Debian` skip would hide a missing baseline.

1. Class C (Faris): `tests/os/{arch,fedora,nixos}.goss.yaml.j2` baselines and the lint os-family mapping; no guest of
   these OSes passes lint until they exist.
2. `base_common`: replace the apt task with a per-family include (`tasks/pkg_{debian,archlinux,redhat}.yml` via
   `ansible_facts.os_family`) using `apt` (keep `lock_timeout`), `community.general.pacman`, `ansible.builtin.dnf`;
   define `base_packages` per family in group_vars (names differ).
3. `timezone` and `hostname` modules work on systemd distros; on NixOS they conflict with the declarative config, so
   NixOS gets its own `os_nixos` base instead of `base_common`.
4. Review `admin_secret.yml` for Debian-only assumptions.
5. Patching: define pacman / dnf / nix equivalents. Fedora has `dnf updateinfo` security advisories; Arch and NixOS
   have no severity feed (treat as Sev3, reboot gate stays with Faris).
6. Build the templates (host-level, Faris), add the ACL per template, then deploy one stg throwaway guest per OS.
