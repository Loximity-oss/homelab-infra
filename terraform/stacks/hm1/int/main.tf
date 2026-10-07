# Identical in every zone stack: the zone comes from the directory name.
# Guests are declared as YAML files in ./guests/<hostname>.yaml (see README).
terraform {
  required_version = ">= 1.9"
  backend "pg" {} # conn_str and schema_name passed by CI (-backend-config)
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.115"
    }
  }
}

variable "standards_dir" {
  description = "Checkout of homelab-standards (CI sets TF_VAR_standards_dir)."
  type        = string
}

provider "proxmox" {
  endpoint = "https://192.168.67.2:8006/"
  insecure = true # self-signed until the internal PKI exists
  # api_token comes from PROXMOX_VE_API_TOKEN
}

locals {
  zone      = basename(abspath(path.module))
  zones     = yamldecode(file("${var.standards_dir}/naming/zones.yaml"))
  protected = yamldecode(file("${var.standards_dir}/protected.yaml"))
  specs = {
    for f in fileset("${path.module}/guests", "*.yaml") :
    trimsuffix(f, ".yaml") => yamldecode(file("${path.module}/guests/${f}"))
  }
  keys = [for f in sort(fileset("${path.module}/../../../../ansible/keys", "*.pub")) : trimspace(file("${path.module}/../../../../ansible/keys/${f}"))]
}

module "guests" {
  source          = "../../../modules/lxc-guests"
  zone            = local.zone
  zone_cfg        = local.zones.zones[local.zone]
  protected       = local.protected
  specs           = local.specs
  ssh_public_keys = local.keys
}

output "guests" {
  value = module.guests.guests
}
