# host/ — root-side helpers on hm1-mgt-pve01 (Class C)

The pipeline never runs as root. Proxmox reserves a few actions for `root@pam` (container feature flags other than
nesting), so a narrow root helper on the host does them on the pipeline's behalf (pull model: nothing connects in).

| File | Installed to |
|---|---|
| `pve-feature-broker/pve-feature-broker` | `/usr/local/sbin/pve-feature-broker` (root, 0755) |
| `pve-feature-broker/pve-feature-broker.json` | `/etc/pve-feature-broker.json` |
| `pve-feature-broker/pve-feature-broker.{service,timer}` | `/etc/systemd/system/` (timer enabled, every minute) |

**How keyctl works:** a guest spec sets `keyctl: true` → the module creates the container stopped, with nesting and the tag
`feat-keyctl` → within a minute the broker sets `keyctl=1` and starts it (a running container is rebooted once) →
Ansible (waiting for SSH up to 5 min) continues. The broker refuses protected VMIDs, VMIDs outside guest-zone blocks,
privileged containers and vmbr0. Changing anything here is Class C; root installs it (it is not deployed automatically).
Log: `journalctl -t pve-feature-broker`.
