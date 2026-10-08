# Ubuntu (and later Windows) VMs cloned from a protected template (design-v7 decision 32).
# Baseline sizing when the spec leaves it out (D6): 2 vCPU / 2 GB / 16 GB on SATA. Workload sizing is set in the spec
# by the Configuration Engineer. Templates are clone-only for the apply token (protected.yaml `templates`).
locals {
  prefix          = split("/", var.zone_cfg.cidr)[1]
  protected_vmids = [for k, _ in try(var.protected.vmids, {}) : tonumber(k)]
  template_range  = try(var.protected.templates, [9000, 9199])
  is_stg          = var.zone == "stg"

  guests = {
    for h, s in var.specs : h => merge(s, {
      cores     = try(s.cores, var.default_cores)
      memory_mb = try(s.memory_mb, var.default_memory_mb)
      disk_gb   = try(s.disk_gb, var.default_disk_gb)
      datastore = try(s.datastore, var.default_datastore)
      tags = sort(distinct(compact([
        "zone-${var.zone}",
        "role-${s.role}",
        "mgd-tf",
        "owner-${s.owner}",
        "os-${s.os}",
        "patch-${s.patch}",
        "env-${try(s.env, local.is_stg ? "staging" : "prod")}",
      ])))
    })
  }
}

resource "proxmox_virtual_environment_vm" "guest" {
  for_each = local.guests

  node_name     = var.node_name
  vm_id         = each.value.vmid
  name          = each.key
  description   = trimspace("${try(each.value.description, "")}\nManaged by homelab-infra (terraform/stacks/hm1/${var.zone}/guests/${each.key}.yaml)")
  tags          = each.value.tags
  pool_id       = var.zone_cfg.pool
  on_boot       = true
  started       = true
  machine       = "q35"
  scsi_hardware = "virtio-scsi-single"

  clone {
    vm_id        = var.vm_templates[each.value.os]
    full         = true
    datastore_id = each.value.datastore
    node_name    = var.node_name
  }

  agent {
    enabled = true
    trim    = true
    timeout = "10m"
  }

  cpu {
    cores = each.value.cores
    type  = "x86-64-v2-AES"
  }

  memory {
    dedicated = each.value.memory_mb
    floating  = 0
  }

  disk {
    datastore_id = each.value.datastore
    interface    = "scsi0"
    size         = each.value.disk_gb
    discard      = "on"
    iothread     = true
    ssd          = true
  }

  network_device {
    bridge   = var.zone_cfg.bridge
    vlan_id  = var.zone_cfg.vlan
    model    = "virtio"
    firewall = false
  }

  operating_system {
    type = "l26"
  }

  serial_device {
    device = "socket"
  }

  vga {
    type = "serial0"
  }

  initialization {
    datastore_id = each.value.datastore
    upgrade      = false # Ansible owns packages

    ip_config {
      ipv4 {
        address = "${each.value.ip}/${local.prefix}"
        gateway = var.zone_cfg.gateway
      }
    }

    dns {
      domain  = var.domain
      servers = var.dns_servers
    }

    # root + the pipeline keys, like LXC guests (verified on a throwaway clone of 9000, 2026-10-08).
    user_account {
      username = "root"
      keys     = var.ssh_public_keys
    }
  }

  lifecycle {
    # Pool membership is set at create time (same as lxc-guests). The clone source never changes a running VM.
    ignore_changes = [pool_id, clone]

    precondition {
      condition     = !contains(local.protected_vmids, each.value.vmid)
      error_message = "VMID ${each.value.vmid} is protected (homelab-standards/protected.yaml)."
    }
    precondition {
      condition     = each.value.vmid < local.template_range[0] || each.value.vmid > local.template_range[1]
      error_message = "VMID ${each.value.vmid} is in the template range (protected.yaml templates); templates are never managed."
    }
    precondition {
      condition     = !contains(try(var.protected.bridges, []), var.zone_cfg.bridge)
      error_message = "Bridge ${var.zone_cfg.bridge} is protected (management network)."
    }
    precondition {
      condition     = var.zone_cfg.guests == true
      error_message = "Zone ${var.zone} does not take pipeline guests."
    }
    precondition {
      condition     = contains(keys(var.vm_templates), each.value.os)
      error_message = "No VM template for os '${each.value.os}' (vm_templates)."
    }
    precondition {
      condition     = each.value.disk_gb >= var.min_disk_gb
      error_message = "disk_gb ${each.value.disk_gb} is smaller than the template disk (${var.min_disk_gb} GB)."
    }
  }
}
