# whoami (design note)

Purpose: test web app (traefik/whoami) on hm1-stg-web01 to exercise the guest -> Ansible -> test flow. First CD-tested deploy in Staging.

- Guest: hm1-stg-web01, VMID 811, 192.168.76.10, zone stg (VLAN 60), role web, LXC 1 core / 1 GB / 10 GB, `keyctl: true`.
- Image: traefik/whoami:v1.10.3 via docker compose in /opt/whoami (role app_whoami, stack apps/whoami/compose.yaml.j2).
- Port: 8080/tcp on the guest -> container 80. No DNS name (not in services.yaml), no TLS (test, staging only).
- Data: stateless; nothing to back up. (Guest disk is generally not backed up, open item A-1.)
- Secrets: OpenBao stg/secret/apps/whoami, key `test_token` (test value). Not consumed by the app; stored to validate the flow.
- Upgrade: bump app_whoami_image in role defaults via PR; handler recreates the container.
- Healthcheck: HTTP GET http://127.0.0.1:8080/ returns 200 (also run by the role).
- Docker in an unprivileged LXC needs keyctl: the spec sets `keyctl: true`, Terraform creates the guest stopped and tagged `feat-keyctl`, and the host's pve-feature-broker enables nesting+keyctl and starts it.
