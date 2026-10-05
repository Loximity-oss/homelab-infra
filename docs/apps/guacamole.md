# Apache Guacamole + cloudflared (hm1-dmz-guac01)

Status: design, ships in PR #3 with the guest. Role: `app_guac`. Config: `apps/guacamole/`. Runbook: `docs/apps/guacamole-runbook.md`.

## Purpose and pattern
Browser-based remote access gateway (RDP/SSH/VNC) published at https://jb.loximity.dev through a remotely-managed Cloudflare tunnel.

**One app, one tunnel (onebox):** this guest hosts the application and its own dedicated cloudflared tunnel. Each published application gets its own container and its own tunnel; tunnels are not shared between apps. No inbound port is opened anywhere; cloudflared dials out to Cloudflare.

## Guest
hm1-dmz-guac01, VMID 210, 192.168.71.10, DMZ VLAN 10, role guac, Ubuntu 26.04 unprivileged LXC, 2 cores / 2 GB / 16 GB (enough: postgres + guacd + Tomcat ~1.2 GB). Never on vmbr0.
Service DNS: `jb.loximity.dev` is an external Cloudflare name (services.yaml covers lox-internal.dev only).

## Install method
Containers (Docker Engine from Ubuntu `docker.io` + `docker-compose-v2`): distros no longer ship the Guacamole web app, and a native build means Tomcat + compiling guacd. Official images, pinned: `guacamole/guacamole:1.6.0`, `guacamole/guacd:1.6.0`, `postgres:17-alpine`. cloudflared is a native package (Cloudflare apt repo) run by its own systemd unit. No bb_* building blocks exist yet, so the role is self-contained (candidates: bb_docker, bb_cloudflared).

### LXC features: nesting only
The guest relies on the module default `nesting=1`; no `keyctl` is requested. I cannot show keyctl is required: the stack uses only bridge networking and plain volumes, and nothing in it uses kernel keyrings. Proxmox guidance commonly lists keyctl alongside nesting for Docker, because runc creates a session keyring and that can be denied in an unprivileged container, so this is a real runtime risk, not proven either way. Design is nesting-only; the smoke test below reveals a failure. If containers fail to start with a keyring/permission error from runc, the fix is a module option for keyctl (Infrastructure Engineer, Class B/C), not a workaround in the role.

## Ports
| Port | Bind | Use |
|---|---|---|
| 8080/tcp | 127.0.0.1 only | Guacamole web (served at `/`, WEBAPP_CONTEXT=ROOT) |
| 4822, 5432 | private docker network 172.30.0.0/24 | guacd, postgres; not published |
| outbound 7844 tcp/udp | to Cloudflare | tunnel |

Nothing listens on 192.168.71.10. DMZ policy (Network Engineer): allow egress 7844 to Cloudflare, DNS, and only the specific RDP/SSH targets.

## Cloudflare dashboard (Faris)
Zero Trust > Networks > Tunnels > the jb tunnel > Public Hostname:
- Hostname `jb.loximity.dev`, path empty
- Service type `HTTP`, URL `http://localhost:8080`
- Not https, and no `/guacamole` path.
Recommended: a Zero Trust Access application on `jb.loximity.dev` (Faris identity only, MFA required).

## Auth, secrets, MFA
- Guacamole postgres auth + TOTP extension (`TOTP_ENABLED=true`); enrollment forced at first login.
- Tunnel token: stored in OpenBao (path and delivery settled separately). The role's interface is the env var `APP_GUAC_CLOUDFLARED_TOKEN` at apply time; it is written to root-only `/etc/cloudflared/token.env` (0600), loaded via systemd EnvironmentFile as `TUNNEL_TOKEN`. Never in the repo or process arguments.
- DB password: generated on the guest on first run, stored in `/etc/guacamole/guac.env` (0600).
- Admin: default `guacadmin/guacadmin` is replaced during first apply, before cloudflared starts, by a random password in `/root/.guacamole-admin-initial` (0600). Faris reads it once (via SRE), logs in, enrolls TOTP, creates a personal admin, disables guacadmin, deletes the file.
- Hardening: localhost-only bind, RemoteIpValve trusts only the docker bridge, `no-new-privileges`, pinned images, only the TOTP extension, cloudflared systemd sandboxing.

## Data
`/var/lib/guacamole/pgdata` (users, connections, TOTP secrets) is the only state. No backups are configured by decision of Faris; the config is reproducible from code, but users, connections and TOTP enrollments would be lost with the guest and must be recreated.

## Upgrade
Bump pinned tags in `apps/guacamole/docker-compose.yml` (and `app_guac_image_version`), PR, apply. Postgres major bumps need a dump/restore by hand. Read Guacamole release notes for schema upgrade scripts. cloudflared updates via apt (patching ring).

## Needed from other agents
- Infrastructure: secret delivery to the apply as `APP_GUAC_CLOUDFLARED_TOKEN`.
- Systems Engineer: base hardening (ssh, unattended upgrades, time sync, optional in-guest firewall); confirm Ubuntu 26.04 apt reaches docker.io and pkg.cloudflare.com.
- Network: DMZ egress rules; no DMZ to management route.

## Smoke test (after PR #3 merge and apply; guest does not exist before)
1. `systemctl is-active docker guacamole-stack cloudflared` all active.
2. Nesting-only check: `docker compose -f /opt/guacamole/docker-compose.yml ps --format '{{.Service}} {{.State}}'` shows postgres, guacd, guacamole all `running` (none restarting/exited), and `docker run --rm hello-world` or `docker run --rm guacamole/guacd:1.6.0 true` succeeds. Check `journalctl -u docker -u guacamole-stack | grep -i -E 'keyring|keyctl|permission denied'` is empty. A runc keyring error here means keyctl is needed.
3. `ss -ltn` shows 8080 only on 127.0.0.1.
4. `curl -sI http://127.0.0.1:8080/` returns 200.
5. cloudflared journal shows registered tunnel connections.
6. `curl -sI https://jb.loximity.dev` returns 200 (or an Access login redirect).
7. Login with the initial admin; TOTP enrollment prompt appears.
