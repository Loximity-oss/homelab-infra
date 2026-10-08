# whoami runbook

Host: hm1-stg-web01 (VMID 811, 192.168.76.10, zone stg).

- Check: `curl -s -o /dev/null -w '%{http_code}' http://192.168.76.10:8080/` -> 200.
- Status: `docker compose -f /opt/whoami/compose.yaml ps`; logs: `docker compose -f /opt/whoami/compose.yaml logs --tail 50`.
- Restart: `docker compose -f /opt/whoami/compose.yaml up -d --force-recreate`.
- Redeploy / upgrade: change `app_whoami_image` in ansible/roles/app_whoami/defaults/main.yml by PR.
- Down: container not running -> check `systemctl status docker`, then restart as above.
- Data/backup: stateless.
