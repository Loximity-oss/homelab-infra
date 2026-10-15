# NixOS template configuration for 9003 (tpl-nixos2605-base). Built by scripts/template-build.py with
# nixos-generators --format qcow, which provides the disk layout and the boot loader. Requirements: docs/apps/vm-templates.md.
# Changing this file is Class C and rebuilds 9003.
{ config, pkgs, lib, ... }:
{
  boot.kernelParams = [ "console=ttyS0,115200" "console=tty0" ];
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
  environment.systemPackages = with pkgs; [ python3 ];
  networking.useDHCP = false;         # cloud-init configures the interface
  networking.hostName = "";           # set per clone by cloud-init
  system.stateVersion = "26.05";
}
