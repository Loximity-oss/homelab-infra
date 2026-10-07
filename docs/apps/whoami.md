# whoami (design note)

Purpose: test web app (traefik/whoami) on hm1-lab-web01 to exercise the guest -> Ansible -> healthcheck flow.

- Guest: hm1-lab-web01, VMID 810, 192.168.72.240, zone lab, role web, LXC 1 core / 1 GB / 10 GB.
- Image: traefik/whoami:v1.10.3 via docker compose in /opt/whoami (role app_whoami, stack apps/whoami/compose.yaml.j2).
- Port: 8080/tcp on the guest -> container 80. No DNS name (not in services.yaml), no TLS (test, lab only).
- Data: stateless; nothing to back up. (Guest disk is generally not backed up, open item A-1.)
- Secrets: OpenBao lab/secret/apps/whoami, key `test_token` (test value). Not consumed by the app; stored to validate the flow.
- Upgrade: bump app_whoami_image in role defaults via PR; handler recreates the container.
- Healthcheck: HTTP GET http://127.0.0.1:8080/ returns 200 (also run by the role).
- Note: docker in an unprivileged LXC may need nesting/keyctl features on the guest spec; if the apply fails on docker start, ask the Infrastructure Engineer.
