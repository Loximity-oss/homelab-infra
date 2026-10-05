# Apache Guacamole + cloudflared (hm1-dmz-guac01)

Status: design, ships in PR #3 with the guest. Role: `app_guac`. Config: `apps/guacamole/`. Runbook: `docs/apps/guacamole-runbook.md`.

## Purpose
Browser-based remote access gateway (RDP/SSH/VNC) published at https://jb.loximity.dev through a remotely-managed Cloudflare tunnel. No inbound port is opened anywhere; cloudflared dials out to Cloudflare.

## Guest
hm1-dmz-guac01, VMID 210, 192.168.71.10, DMZ VLAN 10, role guac, Ubuntu 26.04 LXC, 2 cores / 2 GB / 16 GB (enough: postgres + guacd + Tomcat ~1.2 GB). Never on vmbr0.
Service DNS: `jb.loximity.dev` is an external Cloudflare name (services.yaml covers lox-internal.dev only). Decision for Faris: whether an internal alias is wanted (would need a standards issue).

## Install method
Containers (Docker Engine from Ubuntu `docker.io` + `docker-compose-v2`): distros no longer ship the Guacamole web app, and a native build means Tomcat + compiling guacd. Official images, pinned: `guacamole/guacamole:1.6.0`, `guacamole/guacd:1.6.0`, `postgres:17-alpine`. cloudflared is a native package (Cloudflare apt repo) run by its own systemd unit. No bb_* building blocks exist yet in the repo, so the role is self-contained; candidates to extract later: bb_docker, bb_cloudflared.

LXC requirement (Infrastructure Engineer): unprivileged container needs `features: nesting=1,keyctl=1` for Docker. Must be in the PR #3 guest spec.

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
- Not https, and no `/guacamole` path (app is served at root).
Strongly recommended: a Zero Trust Access application on `jb.loximity.dev` (allow only Faris identity, MFA required), so Cloudflare authenticates before Guacamole's own login.

## Auth, secrets, MFA
- Guacamole postgres auth + TOTP extension (`TOTP_ENABLED=true`); enrollment is forced at first login of every user.
- Tunnel token: `secret/inbox/cloudflared-jb-tunnel` key `token`. Reaches the role as env var `APP_GUAC_CLOUDFLARED_TOKEN` at apply time (mechanism to be confirmed by Infrastructure) and is written to root-only `/etc/cloudflared/token.env` (0600), loaded via systemd EnvironmentFile as `TUNNEL_TOKEN`. Never in the repo or in process arguments.
- DB password: generated on the guest on first run, stored in `/etc/guacamole/guac.env` (0600). Not in repo.
- Admin: the default `guacadmin/guacadmin` is replaced during first apply, before cloudflared starts, by a random password in `/root/.guacamole-admin-initial` (0600). Faris reads it once (via SRE), logs in, enrolls TOTP, creates a personal admin, disables guacadmin, deletes the file.
- Hardening: localhost-only bind, RemoteIpValve trusts only the docker bridge, `no-new-privileges`, pinned images, only the TOTP extension, cloudflared systemd sandboxing.

## Data and backup
- `/var/lib/guacamole/pgdata` (users, connections, TOTP secrets) is the only state.
- Nightly `pg_dump` (timer) to `/var/backups/guacamole`, 7 kept, 0700. Whole-guest backup (PBS, SRE/Infra) covers dumps and `/etc/guacamole`.
- Restore: see runbook.

## Upgrade
Bump pinned tags in `apps/guacamole/docker-compose.yml` (and the initdb image tag in the role), PR, apply. Postgres major bumps need dump/restore. Read Guacamole release notes for schema upgrade scripts. cloudflared updates via apt (patching ring).

## Needed from other agents
- Infrastructure: nesting+keyctl in the spec; secret delivery to the apply as `APP_GUAC_CLOUDFLARED_TOKEN`; PBS backup job.
- Systems Engineer: base hardening (ssh, unattended upgrades, time sync, optional in-guest firewall); confirm Ubuntu 26.04 apt reaches docker.io and pkg.cloudflare.com.
- Network: DMZ egress rules; no DMZ to management route.

## Smoke test (after PR #3 merge and apply; guest does not exist before)
1. `systemctl is-active docker guacamole-stack cloudflared` all active.
2. `ss -ltn` shows 8080 only on 127.0.0.1.
3. `curl -sI http://127.0.0.1:8080/` returns 200.
4. cloudflared journal shows registered tunnel connections.
5. `curl -sI https://jb.loximity.dev` returns 200 (or an Access login redirect).
6. Login with the initial admin; TOTP enrollment prompt appears.
