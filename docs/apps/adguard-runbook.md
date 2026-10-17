# AdGuard Home — runbook

Hosts: every `role: dns` guest (group `role_dns`); today `hm1-iot-dns01/02` (VMID 601/602) and `hm1-wifi-dns01/02` (VMID 501/502). Role `app_adguard`, files in `apps/adguard/`, shared vars in `ansible/playbooks/group_vars/role_dns.yml`.

## Where config lives and how to change it
- Config is code. Change `apps/adguard/AdGuardHome.yaml.j2` or the vars (`group_vars/role_dns.yml` for all zones; `zone_<z>.yml` / `host_vars/<host>.yml` for overrides) **by PR only**. The apply renders and installs it on every `role_dns` guest and restarts AdGuard only when something differs.
- **Manual edits are reverted.** Editing `AdGuardHome.yaml` on a guest or in the UI (users, DHCP, clients, rewrites, rules, filters, upstreams, retention) is detected at the next apply (nightly drift or any merge), overwritten from the template, and AdGuard restarts. The apply log shows the reverted key paths. If a UI change is wanted, put it in the PR. Query log and stats data are not affected.
- Never log in to edit config; for a hotfix during an incident use the UI/ssh, then codify via PR before the next apply.

## Status report in the apply log
Every apply of a `role_dns` guest ends with a read-only task `AdGuard status | report` (tag `adguard_status`). It is the way to see the guests when SSH from CT 100 is firewalled: applies run from CT 300. Find it in the apply log of the merged PR (and in the nightly drift apply log), one line per host:

`ADGUARD-STATUS hm1-iot-dns01: service=active rules=[AdGuard DNS filter=<n>, Phishing URL Blocklist (PhishTank/OpenPhish)=<n>, Malicious URL Blocklist (URLhaus)=<n>, Stalkerware Indicators List=<n>] journal_err_10m=0 drift_rc=0 dig=ok <ip> OK`

- `service`: `systemctl is-active AdGuardHome`. Anything but `active` fails the play.
- `rules`: rule count per enabled filter, counted from `/opt/AdGuardHome/data/filters/<id>.txt` (lines that are not blank, not comments `!`/`#` and not the `[Adblock Plus ...]` header). Rough expectation (2026-10-17): AdGuard DNS filter in the 100k range, Phishing ~10k+, URLhaus ~3.5k+, Stalkerware ~1.3k. An enabled filter with 0 rules (or missing file) after ~60s of retries fails the play; the failure message names the host and the counts, and the report line is printed before it.
- Lists are downloaded only on a new guest or when a filter has a new id. An existing guest loads its lists from disk at start and keeps the old file if a refresh fails, so a count there can be stale rather than fresh. The 60s wait matters mainly for new guests and new ids.
- **Swapping a list under the same id** (changing the URL of an existing id) keeps showing the old file's count until AdGuard refreshes it (its periodic filter update, or an update in the UI). When swapping, use a new filter id so the new list is downloaded at once, and remove the old one. Retired ids (2) are not reused.
- **Removed filters:** a filter taken out of `app_adguard_filters` drops out of the managed config at the next apply, but its file `/opt/AdGuardHome/data/filters/<id>.txt` stays on disk (the role does not delete it). It is not loaded, so harmless; `rm` it by hand to free the space.
- `journal_err_10m`: lines matching `error|failed` in `journalctl -u AdGuardHome --since -10min`. Not fatal. Non-zero usually means a failed list download or upstream problem: read the journal on the guest.
- `drift_rc`: the drift script run after the converge, managed vs live config. 0 in sync, 1 drifted keys, 2 cannot compare. Expected 0 after a normal apply; not fatal.
- `dig`: answer for example.com from 127.0.0.1, `FAIL` when none. Not fatal here (the healthcheck earlier in the role already retries).
- Last word `OK` = nothing to look at; `WARN` = at least one of journal errors, drift rc != 0 or dig FAIL. The play fails (with a message naming the host) only for the two hard conditions above. Rollout is serial: 1, so a failing host stops the rollout before the next.
- No credentials are involved (counts come from files, not the admin API), so nothing sensitive is in the log.
- The report only runs as part of an apply (it is the last block of the role, merge or nightly drift apply); there is no separate way to trigger it. To look at a guest between applies use the commands under Check (from CT 300).

## Check
- `systemctl is-active AdGuardHome`
- `dig +short @127.0.0.1 example.com` (answer expected); from a client in the same zone `dig @<guest ip> example.com`
- `curl -sI http://127.0.0.1/` (200 or 302)
- `journalctl -u AdGuardHome -n 50 --no-pager`
- Drift now, read-only: `hm1-adguard-drift /opt/AdGuardHome/conf/AdGuardHome.yaml.managed /opt/AdGuardHome/conf/AdGuardHome.yaml; echo rc=$?` (0 in sync, 1 lists drifted key paths, 2 cannot compare)
- Memory and blocklists: `free -m` and `systemctl status AdGuardHome` (memory line) on the guest; guests have 1 GiB. Rule count per list: the status report above, or the UI (Filters > DNS blocklists).

## Common problems
- **Service fails with "address already in use" on :53**: the resolved stub is back. Check `/etc/systemd/resolved.conf.d/adguardhome.conf`, `ss -lntup | grep :53`, re-run the apply.
- **Guest cannot resolve (apt/download)**: `/etc/resolv.conf` must not point at 127.0.0.53; it should link to `/run/systemd/resolve/resolv.conf`, or use AdGuard on 127.0.0.1.
- **Upstream (DoH) failures**: check the guest reaches 1.1.1.1 / 9.9.9.9 and TCP 443 outbound (UniFi rules for the guest's zone).
- **Status fails with an enabled filter at 0 rules**: the list download failed (DNS/egress to the list host, `adguardteam.github.io`, or too little memory). Check `journalctl -u AdGuardHome` and egress rules, then re-run the apply.
- **Memory tight / OOM**: check `free -m` and the AdGuard memory line first. The lists are small; the AdGuard DNS filter (id 1) is the largest. Raise the guest memory (Terraform spec, Infrastructure Engineer) or drop a list from `role_dns.yml` by PR. Do not add the Threat Intelligence Feeds list (`tif.txt`, about 1.95M rules): too large for these guests.
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
