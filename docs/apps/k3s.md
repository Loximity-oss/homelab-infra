# k3s HA cluster - moved to homelab-apps

Since the cutover (infra #127, step 10) the k3s cluster (hm1-shr-k3sc01..03, hm1-shr-k3sw01..02; VMs 310-314) is defined and deployed by **homelab-apps**: `catalog/k3s/` (role, templates, L4 checks, health) and `deployments/shr/k3s.yaml` (`api_vip`, `vrrp_router_id`, taints).

Design note, upgrade and operation: homelab-apps `catalog/k3s/` and its README (http://192.168.70.20:3000/homelab/homelab-apps). The guests themselves (sizing, IPs) stay in `terraform/stacks/hm1/shr/guests/`.

Removed from this repo: role `svc_k3s`, `apps/k3s/`, the `role_k3sc`/`role_k3sw` plays and the token escrow play in `site.yml`, `tests/apps/` L4 checks. Reboot rule for patching: control nodes one at a time after `etcd-snapshot save` (the `reboot` workflow handles the drain).
