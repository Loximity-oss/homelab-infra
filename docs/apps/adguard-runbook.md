# AdGuard Home — runbook

Hosts: every `role: dns` guest (group `role_dns`); today `hm1-iot-dns01/02` (VMID 601/602) and `hm1-wifi-dns01/02` (VMID 501/502). Role `app_adguard`, files in `apps/adguard/`, shared vars in `ansible/playbooks/group_vars/role_dns.yml`.

## Where config lives and how to change it
- Config is code. Change `apps/adguard/AdGuardHome.yaml.j2` or the vars (`group_vars/role_dns.yml` for all zones; `zone_<z>.yml` / `host_vars/<host>.yml` for overrides) **by PR only**. The apply renders and installs it on every `role_dns` guest and restarts AdGuard only when something differs.
- **Manual edits are reverted.** Editing `AdGuardHome.yaml` on a guest or in the UI (users, DHCP, clients, rewrites, rules, filters, upstreams, retention) is detected at the next apply (nightly drift or any merge), overwritten from the template, and AdGuard restarts. The apply log shows the reverted key paths. If a UI change is wanted, put it in the PR. Query log and stats data are not affected.
- Never log in to edit config; for a hotfix during an incident use the UI/ssh, then codify via PR before the next apply.

## Check
- `systemctl is-active AdGuardHome`
- `dig +short @127.0.0.1 example.com` (answer expected); from a client in the same zone `dig @<guest ip> example.com`
- `curl -sI http://127.0.0.1/` (200 or 302)
- `journalctl -u AdGuardHome -n 50 --no-pager`
- Drift now, read-only: `hm1-adguard-drift /opt/AdGuardHome/conf/AdGuardHome.yaml.managed /opt/AdGuardHome/conf/AdGuardHome.yaml; echo rc=$?` (0 in sync, 1 lists drifted key paths, 2 cannot compare)

## Common problems
- **Service fails with "address already in use" on :53**: the resolved stub is back. Check `/etc/systemd/resolved.conf.d/adguardhome.conf`, `ss -lntup | grep :53`, re-run the apply.
- **Guest cannot resolve (apt/download)**: `/etc/resolv.conf` must not point at 127.0.0.53; it should link to `/run/systemd/resolve/resolv.conf`, or use AdGuard on 127.0.0.1.
- **Upstream (DoH) failures**: check the guest reaches 1.1.1.1 / 9.9.9.9 and TCP 443 outbound (UniFi rules for the guest's zone).
- **AdGuard restarts at every apply**: a permanent drift. The apply log lists the key path; AdGuard normalises that value differently from the template. Align the value in the vars by PR.
- **AdGuard rejects the config after a template change** (service fails to start, healthcheck fails): revert the PR; the next apply reinstalls the previous config.
- **First apply failed at "Write once to OpenBao"**: policy or token problem (platform): the ci-apply policy needs create on `<zone>/secret/data/apps/adguard/*` and the zone namespace must exist (`apply.sh`). No hash file was written, so the next run retries.
- **Secret exists in OpenBao but `/etc/hm1/adguard-admin.hash` is missing** (apply died between the two steps): the create-only write fails with 400. Faris deletes `<zone>/secret/apps/adguard/<host>` in OpenBao, then re-run the apply.

## Admin password
Stored by Ansible at `<zone>/secret/apps/adguard/<hostname>` (keys `username`, `password`). Agents cannot read it; Faris reads it from OpenBao. Rotation: Faris deletes the secret and `/etc/hm1/adguard-admin.hash` on the guest, then re-run the apply. A password changed in the UI is reverted to this one.

## Upgrade / rollback
Change `app_adguard_version` and `app_adguard_sha256` in `ansible/roles/app_adguard/defaults/main.yml` by PR (checksum from the release `checksums.txt`). Rollback is the reverse PR.

## Data / backup
`/opt/AdGuardHome/data` holds the query log and stats; **not backed up** (open item A-1). Config and binary are rebuilt by the apply.
