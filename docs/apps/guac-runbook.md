# Runbook: Guacamole gateway (hm1-dmz-guac01, CT 210)

See `docs/apps/guac.md` for design.

## Quick status
```
ssh root@192.168.71.10
/usr/local/sbin/guac-smoke
systemctl status guac-db guac-guacd guac-web guac-cloudflared
podman ps
journalctl -u guac-web -n 100 --no-pager
```

## Provide the tunnel token (once Faris has it)
```
umask 077
printf 'TUNNEL_TOKEN=%s\n' '<token>' > /etc/guac/secrets/cloudflared.env
systemctl restart guac-cloudflared
curl -s http://127.0.0.1:2000/ready
```
The unit is skipped (ConditionPathExists) until the file exists. Also store the token in OpenBao as `cloudflared-guac01-tunnel`. In Cloudflare, tunnel public hostname -> service `http://guac-web:8080`.

## Admin password
Show: `cat /etc/guac/secrets/admin-password` (user `guacadmin`). Change: write a new value to that file, then re-run the apply (or `/usr/local/sbin/guac-set-admin-password`). First login enrols TOTP.

## Backup and restore
```
systemctl start guac-backup.service     # dump now
ls -l /var/backups/guac/
# restore
systemctl stop guac-web
zcat /var/backups/guac/<file>.sql.gz | podman exec -i guac-db psql -U guacamole -d guacamole_db
systemctl start guac-web
```

## Upgrade
PR bumping `app_guac_*_version` in `ansible/roles/app_guac/defaults/main.yml`; run backup first; check release notes for schema upgrades; verify with guac-smoke.

## Troubleshooting
| Symptom | Check |
|---|---|
| `podman info` fails / containers will not start | LXC features nesting=1,keyctl=1 missing on CT 210 |
| guac-web restarts | `journalctl -u guac-web`; DB reachable? `podman exec guac-db pg_isready` |
| 502 via Cloudflare | `curl 127.0.0.1:2000/ready`; tunnel public hostname service URL; egress 7844 |
| Blank web page / 404 | URL must be `/` (WEBAPP_CONTEXT=ROOT) |
| Client IP shows Podman IP | RemoteIpValve regex vs network subnet 10.89.210.0/24 |
