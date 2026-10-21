# AdGuard Home - moved to homelab-apps

Since the cutover (infra #127, step 10) AdGuard Home on every `role: dns` guest (wifi: hm1-wifi-dns01/02, iot: hm1-iot-dns01/02) is defined and deployed by **homelab-apps**: `catalog/adguard/` (role, templates, L4 checks, health) and `deployments/<zone>/adguard.yaml` (per-zone values).

Design note, upgrade and operation: homelab-apps `catalog/adguard/` and its README (http://192.168.70.20:3000/homelab/homelab-apps). Version bumps are PRs there.

Removed from this repo: role `app_adguard`, `apps/adguard/`, `group_vars/role_dns.yml`, the `role_dns` play in `site.yml`, `tests/apps/` L4 checks. Patch health comes from the catalogue.
