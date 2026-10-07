# apps/ — service and application stacks (owner: Configuration Engineer, oc-cfg)

One directory per app: `apps/<name>/` holds the config files and stack definitions the role `ansible/roles/app_<name>`
renders onto the guest (templated `*.j2`): e.g. `compose.yaml.j2`, `<app>.yaml.j2`, systemd units.

| Piece | Where |
|---|---|
| Guest | `terraform/stacks/hm1/<zone>/guests/<host>.yaml` (Infrastructure Engineer) |
| Logic | `ansible/roles/app_<name>/` (services: `svc_<name>`) |
| Config / stack | `apps/<name>/` |
| Docs | `docs/apps/<name>.md` (design note), `docs/apps/<name>-runbook.md` |
| Secrets | OpenBao `<zone>/secret/apps/<name>` — stored create-only by oc-cfg, rendered by Ansible at deploy; never in Git |
| TLS | ACME from OpenBao PKI (role `<zone>`); key generated on the guest |
| Data | guest disk — not backed up (open item A-1) |

Classes: new app on its own guest = A · on a shared service/role = B · destroy = C. See `apps/_example/` and `ansible/roles/app_example/`.
