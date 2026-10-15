# AdGuard Home — runbook

Hosts: every `role: dns` guest (group `role_dns`); today `hm1-iot-dns01` (VMID 601), `hm1-iot-dns02` (VMID 602). Role `app_adguard`, files in `apps/adguard/`.

## Check
- `systemctl is-active AdGuardHome`
- `dig +short @127.0.0.1 example.com` (answer expected); from a client in the same zone `dig @<guest ip> example.com` (iot: 192.168.74.10/.11)
- `curl -sI http://127.0.0.1/` (200 or 302)
- `journalctl -u AdGuardHome -n 50 --no-pager`

## Common problems
- **Service fails with "address already in use" on :53**: the resolved stub is back. Check `/etc/systemd/resolved.conf.d/adguardhome.conf`, `ss -lntup | grep :53`, re-run the apply.
- **Guest cannot resolve (apt/download)**: `/etc/resolv.conf` must not point at 127.0.0.53; it should link to `/run/systemd/resolve/resolv.conf`, or use AdGuard on 127.0.0.1.
- **Upstream (DoH) failures**: check the guest reaches 1.1.1.1 / 9.9.9.9 and TCP 443 outbound (UniFi rules for the guest's zone).
- **First apply failed at "Write once to OpenBao"**: policy or token problem (platform): the ci-apply policy needs create on `<zone>/secret/data/apps/adguard/*` (it has `+/…`, all zones) and the zone namespace must exist (`apply.sh`). No hash file was written, so the next run retries.
- **Secret exists in OpenBao but `/etc/hm1/adguard-admin.hash` is missing** (apply died between the two steps): the create-only write now fails with 400. Faris deletes `<zone>/secret/apps/adguard/<host>` in OpenBao, then re-run the apply.
- **Config change from the template not live**: compare `sha256sum /opt/AdGuardHome/conf/AdGuardHome.yaml.managed /opt/AdGuardHome/conf/AdGuardHome.yaml.applied`; if they differ the next apply installs it and restarts.

## Admin password
Stored by Ansible at `<zone>/secret/apps/adguard/<hostname>` (keys `username`, `password`). Agents cannot read it; Faris reads it from OpenBao. Rotation: Faris deletes the secret and `/etc/hm1/adguard-admin.hash` on the guest, then re-run the apply (a new password is generated and stored).

## Upgrade / rollback
Change `app_adguard_version` and `app_adguard_sha256` in `ansible/roles/app_adguard/defaults/main.yml` by PR (checksum from the release `checksums.txt`). Rollback is the reverse PR. Config is rendered from `apps/adguard/AdGuardHome.yaml.j2`.

## Data / backup
`/opt/AdGuardHome/data` holds the query log and stats; **not backed up** (open item A-1). Config and binary are rebuilt by the apply.
