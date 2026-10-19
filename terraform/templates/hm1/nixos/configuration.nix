# NixOS template configuration for 9003 (tpl-nixos2605-base). Built by scripts/template-build.py with nixpkgs' own
# image builder (config.system.build.images.qemu), which provides the disk layout and the boot loader.
# Requirements: docs/apps/vm-templates.md. Changing this file is Class C and rebuilds 9003.
{ config, pkgs, lib, modulesPath, ... }:
{
  # virtio drivers (virtio_pci, virtio_scsi, virtio_blk, ...) in the initrd: the Proxmox disk is virtio-scsi and
  # virtio_scsi is a module, not built in. Without this the initrd never finds root and hangs (template run 251).
  imports = [ (modulesPath + "/profiles/qemu-guest.nix") ];

  # the last console= is /dev/console: ttyS0 last so stage-1/systemd output reaches the Proxmox serial console
  # (serial0 socket, vga serial0); with tty0 last the run-251 hang showed nothing after GRUB.
  boot.kernelParams = [ "console=tty0" "console=ttyS0,115200" ];
  boot.growPartition = true;

  services.cloud-init = {
    enable = true;
    network.enable = true;            # static IP from the Proxmox cloud-init drive
    settings.disable_root = false;    # clones log in as root with the pipeline keys
  };
  services.qemuGuest.enable = true;   # qemu-guest-agent
  services.openssh = {
    enable = true;
    settings = { PermitRootLogin = "prohibit-password"; PasswordAuthentication = false; };
  };
  # The cloud-init module only puts the binary on the units' PATH, not on the system PATH. The builder's seal step
  # (`cloud-init clean`) and the verify step (`cloud-init status`) run it over ssh: without this they fail with
  # "cloud-init: command not found" (template run 204, after #71 fixed the boot).
  environment.systemPackages = with pkgs; [ python3 cloud-init ];  # pkgs.cloud-init: the 26.05 module has no `package` option (template run 299)
  networking.useDHCP = false;         # cloud-init configures the interface
  networking.hostName = "";           # set per clone by cloud-init
  system.stateVersion = "26.05";
}
