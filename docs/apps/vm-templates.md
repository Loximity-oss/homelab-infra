# VM templates: in-guest requirements (cloud-init reference)

Linux templates (VMID 9000-9099) are built and refreshed by the `template` workflow from the reviewed specs in
`terraform/templates/hm1/<vmid>-<name>.yaml`. The lifecycle, spec format, gates, build steps and rollback are in
`docs/templates.md`. Windows 9100-9199 is outside that workflow. Templates are clone-only for Terraform guest applies
(protected.yaml `templates`).

This note only explains why the specs and guests look the way they do, for roles and apps that depend on it.
`terraform/modules/vm-guests/main.tf` clones a template and sets, per guest: q35, virtio-scsi-single, serial0 socket +
`vga serial0`, QEMU agent enabled (waits up to 10 min), cloud-init drive with static IP, DNS, and `user_account root` +
the pipeline SSH keys (`ansible/keys`). Ansible then connects as root over SSH and needs python3.

| VMID | os tag | Template | Spec |
|---|---|---|---|
| 9000 | ubuntu2604 | Ubuntu 26.04 cloud image | reference template (verified 2026-10-08) |
| 9001 | arch | Arch Linux cloud image | `terraform/templates/hm1/9001-tpl-arch-base.yaml` |
| 9002 | fedora44 | Fedora 44 Cloud Base | `terraform/templates/hm1/9002-tpl-fedora44-base.yaml` |
| 9003 | nixos | NixOS (nixpkgs image builder) | `terraform/templates/hm1/9003-tpl-nixos2605-base.yaml` |

Build state of each template is in the `template` workflow result on the PR that changed its spec.

## Common requirements (every template)

Each OS cloud image provides these; the spec `provision` steps only fill gaps, and the build verifies them on a
throwaway clone.

- The disk grows on clone (cloud-init `growpart`/`resizefs`, or the OS equivalent) so `disk_gb` takes effect. The
  template disk (8G) is the lower bound (`min_disk_gb`).
- Packages: `cloud-init`, `qemu-guest-agent`, an SSH server, `python3` (Ansible). `sudo` is not needed (root login).
- Enabled at boot: `sshd`, `qemu-guest-agent`, the cloud-init services (local, init/main, config, final), and
  cloud-init network configuration (static IP from the Proxmox cloud-init drive; DHCP must not be required).
- Root SSH by key: `user_account root` only works if root login is allowed. Cloud images disable it, so the specs set
  `disable_root: false` in cloud-init config and `PermitRootLogin prohibit-password` in sshd. Password login off.
  This is why a spec may carry `seal_user` (the image's default user, used only for the build boot).
- Serial console on ttyS0 (`console=ttyS0,115200 console=tty0`) because the VM has `vga serial0`.
- Hygiene (done by the build): no pre-baked SSH host keys, `cloud-init clean`, empty machine-id, no static
  IP/hostname, no secrets or user keys baked in.

## Per-OS notes

### Arch Linux (9001, os `arch`)

- Ships cloud-init, qemu-guest-agent, openssh and python. Services of interest: `sshd`, `qemu-guest-agent`,
  `cloud-init-local`, `cloud-init-main`, `cloud-init-network`, `cloud-config`, `cloud-final`, `systemd-networkd`,
  `systemd-resolved`.
- Default user `arch`; root login disabled by default (`disable_root: false` in `/etc/cloud/cloud.cfg.d/`).
- Rolling release. Package manager: `pacman`.

### Fedora 44 (9002, os `fedora44`)

- Ships cloud-init, qemu-guest-agent, openssh-server, python3. NetworkManager is the backend (cloud-init drives it).
- Default user `fedora`; `disable_root: false` is set. SELinux stays enforcing. Package manager: `dnf`.

### NixOS (9003, os `nixos`)

No stock cloud-init qcow2 exists, so the workflow builds one with nixpkgs' image builder from the config under
`terraform/templates/hm1/nixos/` at a pinned `nixpkgs_rev`. The config enables cloud-init with network config,
`services.qemuGuest`, openssh (`PermitRootLogin = prohibit-password`, no passwords), python3, `ttyS0` kernel
parameters and root partition auto-resize.

`/etc` is declarative, so durable changes (packages, users, services) on a NixOS guest go through its
`configuration.nix`, not imperative Ansible tasks. A dedicated `os_nixos` role (rendered `configuration.nix` +
`nixos-rebuild switch`) is a follow-up.

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
6. Once the templates are built by the workflow, deploy one stg throwaway guest per OS.
