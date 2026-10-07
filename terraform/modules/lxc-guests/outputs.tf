output "guests" {
  description = "hostname -> {vmid, ip, zone}"
  value = {
    for h, g in local.guests : h => { vmid = g.vmid, ip = g.ip, zone = var.zone }
  }
}
