# AdGuard Home — design note (Configuration Engineer)

| | |
|---|---|
| Guest / zone | `hm1-iot-dns01` (VMID 601, 192.168.74.10) and `hm1-iot-dns02` (VMID 602, 192.168.74.11), zone iot, LXC, role dns, 2 vCPU / 1 GB / 8 GB |
| Service name | none yet (clients use the two IPs as DNS servers; no services.yaml entry) |
| Ports | 53/tcp+udp DNS; 80/tcp admin UI on 0.0.0.0 (plain HTTP, iot only; no TLS yet) |
| Data | `/opt/AdGuardHome/` (root-owned binary; `conf/AdGuardHome.yaml` live config; `data/` query log + stats) — **not backed up** (open item A-1). Config is code, so only query log/stats are lost |
| Ownership | `/opt/AdGuardHome` and the binary are root-owned (dir `root:adguard 0750`); only `conf/` and `data/` are writable by `adguard` (conf/ is a directory so AdGuard can rewrite its YAML atomically) |
| Secrets | names only: `iot/secret/apps/adguard/<hostname>` keys `username`, `password` (create-only, never read by agents). Guest keeps only the bcrypt hash in `/etc/hm1/adguard-admin.hash` (root, 0600) |
| TLS | none for the UI today; the UI is reachable from iot only |
| Upgrade | bump `app_adguard_version` and `app_adguard_sha256` (from the release `checksums.txt`) in `ansible/roles/app_adguard/defaults/main.yml`, PR, apply. The service restarts once |
| Healthcheck | role end: `dig @127.0.0.1 example.com` returns an answer, and `http://127.0.0.1/` returns 200 or 302 |

## Design
- Native binary, no Docker. Release `v0.107.79` (linux_amd64), sha256 `c48f4a43000665484c5ec28177de11a004759b620dae8f77b2aabefc9ef3687f`, taken from the official release `checksums.txt` and verified by `get_url`. The tarball is unpacked whole into `/var/cache/AdGuardHome-<version>/` and the binary copied from there, so the role does not depend on the member-name prefix.
- Runs as user `adguard` with only `CAP_NET_BIND_SERVICE` (needed for ports 53 and 80); `--no-check-update` (upgrades are by PR).
- `systemd-resolved` stub listener is switched off (`/etc/systemd/resolved.conf.d/adguardhome.conf`) and restarted before AdGuard starts; `/etc/resolv.conf` is re-pointed from the stub to `/run/systemd/resolve/resolv.conf` if it was the stub symlink.
- Upstreams: DoH Cloudflare + Quad9, `parallel` mode; bootstrap 1.1.1.1 and 9.9.9.9; cache on (4 MiB); query log 7 days (`168h`).
- Config template `apps/adguard/AdGuardHome.yaml.j2`, schema 34. AdGuard rewrites its YAML (defaults, UI edits), so the role renders to `conf/AdGuardHome.yaml.managed` and replaces the live file only when the managed copy differs from `conf/AdGuardHome.yaml.applied` (sha256 compare; the marker is updated only after a successful install, so a failed run retries instead of losing the update). UI changes are kept until the template changes; make lasting changes in the template.
- Admin password: generated once per guest, written to OpenBao first (create-only, `cas=0`, `no_log`), then bcrypt-hashed on the runner (passlib + bcrypt on CT 300) and only the hash is put on the guest. Same pattern as `base_common/tasks/admin_secret.yml`; uses the short-lived `GUEST_SECRET_TOKEN` of the apply run.
- Inventory: hosts come from the guest specs (`scripts/inventory.py`); the play targets `hm1-iot-dns01,hm1-iot-dns02` by name (both also in group `role_dns`).

## Prerequisite (merge gate)
The first apply writes `iot/secret/data/apps/adguard/<hostname>`. The ci-apply OpenBao policy must allow create on `iot/secret/apps/adguard/*`; this is a separate Class C standards PR (infrastructure-engineer). **This PR must not merge before that policy is applied**, otherwise the first apply fails at "Write once to OpenBao".

## Open points
- No backup of query log/stats (A-1). The UniFi DHCP/DNS settings for iot clients (point to .10/.11) are a network-engineer task.
