# AdGuard Home - runbook

Guests: `hm1-wifi-dns01` (VMID 500), `hm1-wifi-dns02` (VMID 501). Stack in `/opt/adguard`.

## Check

- `docker ps --filter name=adguardhome` shows Up.
- `dig +short @<guest-ip> example.com` returns an address.
- `curl -s -o /dev/null -w '%{http_code}' http://<guest-ip>:3000/login.html` returns 200.
- `docker logs --tail 50 adguardhome`.

## Restart

`docker compose -f /opt/adguard/compose.yaml up -d --force-recreate`

## One resolver down

Clients fail over to the other; fix the failed one, do not touch both at once. If a guest is lost, recreate it by PR (spec + this role rebuild it; stats and query log are lost, no backup, A-1).

## Port 53 already in use

DNS binds to the guest IP and 127.0.0.1 only. Find the other listener with `ss -lntup | grep ':53 '`; systemd-resolved listens on 127.0.0.53 only and does not conflict.

## Change config

PR on `ansible/roles/app_adguard/defaults/main.yml` (upstreams, filters) or `apps/adguard/AdGuardHome.yaml.j2`. Do not rely on UI edits.

## Admin login

OpenBao `wifi/secret/apps/adguard` (admin_user, admin_password); Faris reads it. Rotating: new password into OpenBao by Faris, then re-run Ansible (hash is rendered from it).

## Upgrade / rollback

Change `app_adguard_image` by PR. Rollback by reverting the PR; `conf/` is kept on the guest.
