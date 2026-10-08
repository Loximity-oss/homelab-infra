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
  description = "VM specs keyed by hostname (guests/<hostname>.yaml with type: vm)."
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

# Baseline sizing (decision 32 D6) when the spec leaves it out.
variable "default_cores" {
  type    = number
  default = 2
}

variable "default_memory_mb" {
  type    = number
  default = 2048
}

variable "default_disk_gb" {
  type    = number
  default = 16
}

variable "default_datastore" {
  type    = string
  default = "lvmt-sata01"
}

variable "min_disk_gb" {
  description = "Template disk size; a clone can't be smaller."
  type        = number
  default     = 8
}

variable "vm_templates" {
  description = "os tag value -> template VMID (protected, clone-only)."
  type        = map(number)
  default = {
    ubuntu2604 = 9000
  }
}
