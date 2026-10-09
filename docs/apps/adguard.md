# AdGuard Home - design note (Configuration Engineer)

| | |
|---|---|
| Guest / zone | `hm1-wifi-dns01` (VMID 500, 192.168.69.10) and `hm1-wifi-dns02` (VMID 501, 192.168.69.11), zone wifi, role dns |
| Service name | `dns.lox-internal.dev` is a VIP in services.yaml; the wifi zone has no VIP band, so clients get both IPs (primary, secondary) via DHCP. Open question for Faris/network-engineer. |
| Sizing | 1 vCPU, 1 GB, 8 GB (set in the guest specs) is enough for a home-sized resolver; unprivileged LXC with nesting (default) for Docker |
| Ports | 53 tcp/udp (DNS, bound to the guest IP and 127.0.0.1); 3000 tcp (admin UI, bound to the guest IP) |
| Image | `adguard/adguardhome` pinned in `app_adguard_image` (v0.107.79), Docker, host network |
| Data | `/opt/adguard/conf` (config), `/opt/adguard/work` (stats, query log, filter cache) - **not backed up** (open item A-1) |
| Secrets | names only: `wifi/secret/apps/adguard` keys `admin_user`, `admin_password`; one shared login on both instances; the bcrypt hash is rendered at deploy |
| TLS | none for now (plain DNS; admin UI over HTTP, restricted by firewall). DoH/DoT can follow with ACME from OpenBao PKI |
| Config sync | none at runtime: both guests are rendered from the same `apps/adguard/AdGuardHome.yaml.j2` and role defaults, so they are equivalent by construction. Change upstreams/filters in `ansible/roles/app_adguard/defaults/main.yml` via PR |
| Upgrade | bump `app_adguard_image` by PR; apply recreates the container; do dns02 impact check first (one resolver stays up only if the PR is applied host by host, which the pipeline does not guarantee: expect a few seconds of outage on both) |
| Healthcheck | role: UI returns 200 on `/login.html`, `dig @<ip> example.com` answers. SRE: same, plus `docker ps` |

## Behaviour notes

- The config seed `/opt/adguard/AdGuardHome.seed.yaml` is copied to `conf/AdGuardHome.yaml` only when the seed changes (the container rewrites the live file). UI edits are therefore overwritten the next time the seed changes; make lasting changes in the PR.
- Needs outbound internet from the wifi zone for image pull, DoH upstreams and filter lists.
- Admin UI is not authenticated beyond the login; it must not be reachable from wifi clients. Allow tcp 3000 only from internal admin sources via the firewall (network-engineer).

## Firewall flows needed (network-engineer)

- wifi clients (192.168.69.0/24 DHCP range) -> 192.168.69.10, 192.168.69.11 on 53 tcp/udp (same subnet: traffic is L2 and does not pass the gateway; only matters if client isolation/AP isolation applies).
- guests -> internet: 53/853/443 tcp, 53 udp (upstreams, filters, Docker Hub).
- Admin UI 3000 tcp: from admin sources only; not from wifi clients.
