variable "zone" {
  description = "Zone code (from the stack directory name)."
  type        = string
}

variable "zone_cfg" {
  description = "This zone's entry from homelab-standards naming/zones.yaml."
  type        = any
}

variable "protected" {
  description = "homelab-standards protected.yaml."
  type        = any
}

variable "specs" {
  description = "Guest specs keyed by hostname (guests/<hostname>.yaml)."
  type        = any
}

variable "node_name" {
  type    = string
  default = "hm1-mgt-pve01"
}

variable "domain" {
  type    = string
  default = "lox-internal.dev"
}

variable "dns_servers" {
  description = "Until hm1-shr-dns01/02 exist."
  type        = list(string)
  default     = ["1.1.1.1", "8.8.8.8"]
}

variable "ssh_public_keys" {
  type = list(string)
}

variable "default_datastore" {
  type    = string
  default = "lvmt-nvme01"
}

variable "lxc_templates" {
  description = "os tag value -> LXC template volid."
  type        = map(string)
  default = {
    ubuntu2604 = "local:vztmpl/ubuntu-26.04-standard_26.04-1_amd64.tar.zst"
  }
}
