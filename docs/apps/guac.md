# Design note: Guacamole gateway (hm1-dmz-guac01)

Status: draft for Reviewer. Owner: Application Engineer. Role code `guac` already in homelab-standards (roles.yaml).

## Purpose
Browser-based remote access gateway (RDP/SSH/VNC) published to the internet through a Cloudflare Tunnel. No inbound port is opened on the network: cloudflared dials out to Cloudflare.

## Guest and zone
| | |
|---|---|
| Guest | hm1-dmz-guac01, CT 210, 192.168.71.10, zone dmz (VLAN 10, vmbr1) |
| Size | 2 vCPU, 4 GB RAM, 16 GB disk (guest spec in this PR) |
| OS | ubuntu2604 LXC, rootful Podman from the distro (quadlet / systemd units) |
| LXC features | **nesting=1 and keyctl=1 are required for Podman in an LXC.** The spec tooling cannot set them; the Infrastructure Engineer has filed an issue. Not worked around. The role fails early with a clear message if `podman info` does not work. Apply of this role is blocked until that is resolved. |

Sizing rationale: Postgres ~150 MB, guacd ~100 MB, Tomcat/Guacamole ~600 MB-1 GB, cloudflared ~50 MB. 4 GB leaves headroom for a few dozen sessions. 16 GB disk: images ~1.2 GB, DB tiny, 7 daily dumps.

## Components (all Podman, pinned)
| Container | Image | Role |
|---|---|---|
| guac-db | docker.io/library/postgres:17.11-alpine | Guacamole auth/connection DB |
| guac-guacd | docker.io/guacamole/guacd:1.6.0 | protocol proxy, port 4822 (network-internal only) |
| guac-web | docker.io/guacamole/guacamole:1.6.0 | web app, port 8080, served at `/` (WEBAPP_CONTEXT=ROOT) |
| guac-cloudflared | docker.io/cloudflare/cloudflared:2026.9.3 | tunnel connector (token mode), metrics on 2000 |

Tags were checked on Docker Hub on 2026-10-05. Versions are role defaults (`app_guac_*_version`). `AutoUpdate=none`; upgrades are PRs.

All four sit on one Podman network `guac` (10.89.210.0/24, internal-only, not routed by the LAN). Quadlet units live in `/etc/containers/systemd/`, rendered from `apps/guac/quadlet/*.j2`. Services: `guac-db`, `guac-guacd`, `guac-web`, `guac-cloudflared`.

## Ports
| Port | Where | Purpose |
|---|---|---|
| 8080 | guac-web; published only to 127.0.0.1 on the guest | web UI. Tunnel reaches it over the Podman network |
| 2000 | guac-cloudflared; 127.0.0.1 only | `/ready` for smoke test |
| 4822, 5432 | Podman network only | guacd, Postgres |
| none | LAN | **No port is listening on 192.168.71.10 from the network.** |

Egress needed (firewall, Faris): cloudflared to Cloudflare edge TCP/UDP 7844 (and 443); guacd to the RDP/SSH/VNC targets; DNS; apt and registries (docker.io) for installs.

## Public hostname
Standards give the public pattern `<service>.loximity.dev` and list no `guac` service; Faris decided the DMZ public zone is `jb.loximity.dev`. The hostname used in docs is therefore a **PLACEHOLDER: `guac.jb.loximity.dev`** (standards issue filed). Nothing in code depends on it: the hostname exists only on the Cloudflare side.

Faris sets in Cloudflare Zero Trust: tunnel public hostname `<public hostname>` -> service `http://guac-web:8080` (cloudflared shares the Podman network, so the container name resolves). Recommended: put Cloudflare Access in front.

## Data
| Path | Content |
|---|---|
| Podman volume `guac-pgdata` (/var/lib/containers/storage/volumes) | Postgres data |
| /etc/guac/secrets/ (0700 root) | db-password, db.env, web.env, admin-password, cloudflared.env |
| /var/lib/guac/ | markers (admin password hash, schema init) |
| /var/backups/guac/ | daily `pg_dump` (guac-backup.timer), 7 kept |

## Secrets (no secrets in Git)
- DB password: generated on the guest on first apply (openssl rand), kept in /etc/guac/secrets, never leaves the guest.
- Admin: the `guacadmin/guacadmin` default from the Guacamole schema is **replaced in the same apply, before guac-web is started**. Password comes from /etc/guac/secrets/admin-password (generated at first apply if absent; Faris may overwrite it, the next apply re-hashes it). Smoke test fails if the default hash is still present. TOTP extension (`EXTENSIONS=auth-totp`) is on: every user enrols 2FA at first login.
- Tunnel token: OpenBao secret **cloudflared-guac01-tunnel** (to be set by Faris; suggested path `hm1/dmz/cloudflared-guac01-tunnel`, key `token`; the path pattern requires lowercase/digits/hyphens, final path is Faris's call). The build does not need it. The role writes /etc/guac/secrets/cloudflared.env (`TUNNEL_TOKEN=...`) only when it receives the value (`app_guac_cloudflared_token`) or the file already exists. The unit has `ConditionPathExists=` on that file, so without it the unit is skipped and the play reports `SKIP`, not failure. How secrets reach the guest from OpenBao is not defined in the platform yet (issue filed); interim: Faris creates the file on the guest (runbook).

## Hardening
rootful containers with `NoNewPrivileges`, cloudflared and guacd with `DropCapability=ALL`, cloudflared read-only rootfs, no host ports on the LAN, no auto-update, TOTP, Cloudflare Access recommended.

## Backup
- Logical: `guac-backup.timer` daily pg_dump to /var/backups/guac (connections, users, history).
- Guest: vzdump/PBS snapshot (SRE). Restore: runbook.

## Upgrade
1. Bump version defaults in a PR (check release notes; Guacamole minor upgrades may ship a schema upgrade script `upgrade-pre-<ver>.sql`; apply from `/opt/guacamole/extra/` in the image).
2. Run guac-backup first (runbook). 3. Apply restarts the unit. 4. Smoke test. Rollback: restore previous tag + dump.

## Dependencies / open items
- LXC features nesting+keyctl (infra issue).
- Guest spec description still says "native packages"; should say Podman (Infrastructure Engineer to amend on this PR).
- `guac` not in services.yaml, public DNS pattern for dmz (standards issue).
- No `bb_*` building-block roles exist and `bb_*` is outside the Application Engineer scope; podman setup is inline in `app_guac` for now (tooling issue).
- Secret delivery from OpenBao to guests (platform issue).
- Faris: Cloudflare tunnel, public hostname -> `http://guac-web:8080`, UniFi rules.

## Smoke test
`/usr/local/sbin/guac-smoke` (source `apps/guac/smoke.sh`), run by the role at the end of apply. See runbook.
