locals {
  prefix          = split("/", var.zone_cfg.cidr)[1]
  protected_vmids = [for k, _ in try(var.protected.vmids, {}) : tonumber(k)]
  is_lab          = var.zone == "lab"

  guests = {
    for h, s in var.specs : h => merge(s, {
      tags = sort(distinct(compact([
        "zone-${var.zone}",
        "role-${s.role}",
        local.is_lab ? "mgd-lab" : "mgd-tf",
        "owner-${s.owner}",
        "os-${s.os}",
        "patch-${s.patch}",
        "env-${try(s.env, local.is_lab ? "lab" : "prod")}",
      ])))
    })
  }
}

resource "proxmox_virtual_environment_container" "guest" {
  for_each = local.guests

  node_name     = var.node_name
  vm_id         = each.value.vmid
  description   = trimspace("${try(each.value.description, "")}\nManaged by homelab-infra (terraform/stacks/hm1/${var.zone}/guests/${each.key}.yaml)")
  tags          = each.value.tags
  pool_id       = var.zone_cfg.pool
  unprivileged  = true
  start_on_boot = true
  started       = true

  features {
    nesting = try(each.value.nesting, true)
  }

  cpu {
    cores = each.value.cores
  }

  memory {
    dedicated = each.value.memory_mb
    swap      = try(each.value.swap_mb, 512)
  }

  disk {
    datastore_id = try(each.value.datastore, var.default_datastore)
    size         = each.value.disk_gb
  }

  operating_system {
    template_file_id = var.lxc_templates[each.value.os]
    type             = startswith(each.value.os, "ubuntu") ? "ubuntu" : "debian"
  }

  initialization {
    hostname = each.key

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

    user_account {
      keys = var.ssh_public_keys
    }
  }

  network_interface {
    name     = "eth0"
    bridge   = var.zone_cfg.bridge
    vlan_id  = var.zone_cfg.vlan
    firewall = false
  }

  lifecycle {
    # Pool membership is set at create time; the provider reports a perpetual diff otherwise.
    ignore_changes = [pool_id]

    precondition {
      condition     = !contains(local.protected_vmids, each.value.vmid)
      error_message = "VMID ${each.value.vmid} is protected (homelab-standards/protected.yaml)."
    }
    precondition {
      condition     = !contains(try(var.protected.bridges, []), var.zone_cfg.bridge)
      error_message = "Bridge ${var.zone_cfg.bridge} is protected (management network)."
    }
    precondition {
      condition     = var.zone_cfg.guests == true
      error_message = "Zone ${var.zone} does not take pipeline guests."
    }
  }
}
