# whoami runbook

Host: hm1-lab-web01 (VMID 810, 192.168.72.240).

- Check: `curl -s -o /dev/null -w '%{http_code}' http://192.168.72.240:8080/` -> 200.
- Status: `docker compose -f /opt/whoami/compose.yaml ps`; logs: `docker compose -f /opt/whoami/compose.yaml logs --tail 50`.
- Restart: `docker compose -f /opt/whoami/compose.yaml up -d --force-recreate`.
- Redeploy / upgrade: change `app_whoami_image` in ansible/roles/app_whoami/defaults/main.yml by PR.
- Down: container not running -> check `systemctl status docker`, then restart as above.
- Data/backup: stateless.
