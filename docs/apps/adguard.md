# AdGuard Home — design note (Configuration Engineer)

| | |
|---|---|
| Guest / zone | every guest with `role: dns`, in any zone (group `role_dns`). Today: `hm1-iot-dns01/02` (VMID 601/602, 192.168.74.10/.11, zone iot) and `hm1-wifi-dns01/02` (VMID 501/502, 192.168.69.10/.11, zone wifi); LXC |
| Service name | none yet (clients use the two IPs of their zone as DNS servers; no services.yaml entry) |
| Ports | 53/tcp+udp DNS; 80/tcp admin UI on 0.0.0.0 (plain HTTP, reachable from the guest's own zone only; no TLS yet) |
| Data | `/opt/AdGuardHome/` (root-owned binary; `conf/AdGuardHome.yaml` live config; `data/` query log + stats) — **not backed up** (open item A-1). Config is code, so only query log/stats are lost |
| Ownership | `/opt/AdGuardHome` and the binary are root-owned (dir `root:adguard 0750`); only `conf/` and `data/` are writable by `adguard` |
| Secrets | names only: `<zone>/secret/apps/adguard/<hostname>` keys `username`, `password` (create-only, never read by agents). Guest keeps only the bcrypt hash in `/etc/hm1/adguard-admin.hash` (root, 0600). No secret is in the repo |
| TLS | none for the UI today |
| Upgrade | bump `app_adguard_version` and `app_adguard_sha256` (from the release `checksums.txt`) in `ansible/roles/app_adguard/defaults/main.yml`, PR, apply. The service restarts once |
| Healthcheck | role end: `dig @127.0.0.1 example.com` returns an answer, and `http://127.0.0.1/` returns 200 or 302 |

## Single source of truth
| What | Where |
|---|---|
| Canonical config template (all zones) | `apps/adguard/AdGuardHome.yaml.j2` |
| Shared values: ports, bind, upstreams, bootstrap, cache, query-log/stats retention, blocklists, rewrites, user rules, persistent clients, allow/deny | `ansible/playbooks/group_vars/role_dns.yml` |
| Per-zone overrides (only what truly differs) | `ansible/playbooks/group_vars/zone_<zone>.yml` |
| Per-host overrides (e.g. a bind IP) | `ansible/playbooks/host_vars/<hostname>.yml` |
| Binary version, paths, service user | `ansible/roles/app_adguard/defaults/main.yml` |
| Admin password | generated at first apply, OpenBao `<zone>/secret/apps/adguard/<hostname>`; only the bcrypt hash is on the guest |

Precedence: `zone_<z>` / `host_vars` beat `role_dns`, which beats role defaults. Lists are replaced, not merged, except `app_adguard_rewrites_extra`, `app_adguard_user_rules_extra`, `app_adguard_clients_extra`, which are appended to the shared lists: use those for zone-specific entries. Example `group_vars/zone_wifi.yml`:

```yaml
app_adguard_rewrites_extra:
  - {domain: printer.wifi.lox-internal.dev, answer: 192.168.69.50}
```

Today there are no overrides: iot and wifi get the identical config (only the admin hash differs per host).

## Design
- Native binary, no Docker. Release `v0.107.79` (linux_amd64), sha256 verified by `get_url`. Runs as user `adguard` with only `CAP_NET_BIND_SERVICE`; `--no-check-update` (upgrades are by PR).
- `systemd-resolved` stub listener is switched off and `/etc/resolv.conf` re-pointed if it was the stub symlink.
- Upstreams: DoH Cloudflare + Quad9, `parallel`; bootstrap 1.1.1.1 / 9.9.9.9; cache 4 MiB; query log and stats 7 days (`168h`); two blocklists (AdGuard DNS filter, HaGeZi Multi PRO).
- **Blocklists and memory.** Guests have 768 MiB. HaGeZi Multi PRO (about 202k entries, HaGeZi's general recommendation, low breakage) is the step up chosen over LIGHT (kept as fallback) and NORMAL; PRO++ and ULTIMATE are rejected (false positives, nobody to unblock). Do not add Threat Intelligence Feeds (`tif.txt`, about 1.95M entries): too large. If RAM gets tight, swap filter id 2 for HaGeZi Multi LIGHT (`adblock/light.txt`) in `role_dns.yml`.
- **Convergence.** AdGuard rewrites its own YAML (migrations, filter timestamps, UI saves), so bytes are never compared. The role renders `conf/AdGuardHome.yaml.managed`, then `hm1-adguard-drift` (`apps/adguard/adguard-drift.py`, needs `python3-yaml`) checks that every key set by the template has the same value in the live file (dict keys AdGuard adds itself are ignored; lists must match item by item). On any difference, or no live file, the **whole live file is replaced** by the rendered template and AdGuard is restarted; otherwise nothing changes and nothing restarts. The apply log lists the drifted key paths (never values). The healthcheck (DNS query + web UI) runs after every apply.
- **Validation before install.** The render goes to `AdGuardHome.yaml.managed` (never the live path) and is checked with `AdGuardHome --check-config -c <managed> -w /opt/AdGuardHome`, run as the `adguard` user. If it fails, the task fails: the live file is not touched, nothing restarts, and the rollout stops (see next point). The check is a syntax/schema load by AdGuard itself; it cannot prove that upstreams are reachable (the healthcheck does that after the restart).
- **Serial rollout.** The `AdGuard DNS` play in `ansible/playbooks/site.yml` runs `serial: 1`, `order: sorted`, `any_errors_fatal: true`: one DNS guest at a time (dns01 before dns02 in each zone), and a failure on one host aborts the remaining hosts. At most one resolver is restarted at a time, and the other of a zone pair keeps answering.
- **What is managed (reverted on drift):** `http`, `users`, `dns` (bind, port, upstreams, bootstrap, cache, blocking mode, allowed/disallowed clients), `tls.enabled`, `dhcp.enabled`, `querylog`, `statistics`, `filtering` (enabled, rewrites), `filters`, `user_rules`, `clients.persistent`.
- **UI-managed state:** users (a second user or a changed password), DHCP, persistent clients, rewrites, custom rules, filter lists, upstreams and query-log/stats settings added or changed in the UI are **overwritten** by the template at the next apply, including a changed admin password (the password in OpenBao stays valid). The query log and statistics data in `data/` are not touched.
- **Settings the template does not mention** (for example safe-browsing, parental control, safe search) are not checked for drift, so changing only them does not trigger an install. But whenever any managed key drifts, or the template changes, the whole live file is replaced and these settings **reset to AdGuard defaults**. They do not reliably survive. To keep one, add it to the template and `role_dns.yml`.
- Admin password: generated once per guest, written to OpenBao first (create-only, `cas=0`, `no_log`), then bcrypt-hashed on the runner and only the hash goes to the guest. Same pattern as `base_common/tasks/admin_secret.yml`.
- Inventory: hosts come from the guest specs; the play targets group `role_dns`, so a new DNS guest in any zone gets AdGuard with no playbook change.

## Idempotence (what is verified)
Not testable before merge: there is no stg DNS guest and the wifi/iot guests were unreachable over SSH from CT 100. After the first apply, the SRE runs `hm1-adguard-drift /opt/AdGuardHome/conf/AdGuardHome.yaml.managed /opt/AdGuardHome/conf/AdGuardHome.yaml` on a wifi host (expect rc=0, no output), then a second apply must report `changed=0` for the install task and no restart handler. rc=1 with a key path means AdGuard normalises that value differently from the template (see risk below).

## Audit 2026-10-17
- Template and role defaults held no iot-specific values (no addresses, rewrites or client rules): wifi inherits only zone-neutral settings. Behaviour of iot is unchanged by this move.
- Previously, hand edits survived until the template changed (an `.applied` marker compared template to template). That is replaced by the live-versus-template check above.
- Risk to watch on the first apply: if AdGuard normalises a value (for example `168h`) differently from the template, the check reports a permanent drift and restarts AdGuard at every apply. The debug task shows the key path; fix by aligning the value in `role_dns.yml`.

## Prerequisite (merge gate)
The first apply on a guest writes `<zone>/secret/data/apps/adguard/<hostname>`. The ci-apply OpenBao policy allows create-only on `+/secret/data/apps/adguard/*` (homelab-standards #15). A DNS guest in a new zone needs only that zone's namespace.

## Open points
- No backup of query log/stats (A-1). The UniFi DHCP/DNS settings for each zone's clients and the zone's DNS flows are network-engineer tasks.
