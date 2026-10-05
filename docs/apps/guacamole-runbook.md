# Runbook: Guacamole + cloudflared (hm1-dmz-guac01, VMID 210)

Design: docs/apps/guacamole.md. The guest exists only after PR #3 is merged and applied. (Belongs in docs/runbooks/app-guacamole.md; Infrastructure Engineer may move it.)

## Health
- `systemctl status guacamole-stack cloudflared`
- `docker compose -f /opt/guacamole/docker-compose.yml ps`
- `curl -sI http://127.0.0.1:8080/`
- `journalctl -u cloudflared -n 50`

## Site down
1. Tunnel: `journalctl -u cloudflared`; `systemctl restart cloudflared`. Unauthorized means token revoked or rotated: Infra updates the secret, re-apply.
2. Stack: `docker compose -f /opt/guacamole/docker-compose.yml logs --tail 100`; `systemctl restart guacamole-stack`.
3. Cloudflare 502/1033: public hostname service must be `http://localhost:8080`.

## Admin recovery
Lost TOTP: another admin ticks 'Clear TOTP secret' on the user. Lost all admins: `rm /root/.guacamole-admin-initial; /opt/guacamole/reset-admin.sh` (new random guacadmin password written to that file).

## Backup / restore
Dumps: `/var/backups/guacamole/guacamole-*.sql.gz`.
Restore: `cd /opt/guacamole; docker compose up -d postgres; zcat DUMP | docker compose exec -T postgres psql -U guacamole guacamole`; then `systemctl restart guacamole-stack`. For a clean restore drop and recreate the schema first.

## Upgrade
PR bumping image tags in apps/guacamole/docker-compose.yml; after apply run the smoke test.

## Rotate tunnel token
Infra stores the new token at secret/inbox/cloudflared-jb-tunnel; re-run apply (restarts cloudflared).
