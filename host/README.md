# host/ — root-side helpers on hm1-mgt-pve01 and hm1-shr-cicd01 (Class C)

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

**ARP announce (issue #28):** a guest that re-uses an IP (replace, re-create) is unreachable until the core switch's ARP
entry for that IP expires, because a fresh guest sends nothing on its own. Within a minute of a pipeline guest
(`mgd-tf`) starting, the broker makes it ping its own gateway once per boot (LXC: `pct exec`, VM: guest agent), which
refreshes the entry. Only guests in guest-zone VMID blocks, never protected ones or vmbr0; logged like the rest.


**homelab-apps runner on CT 300 (`apps-runner/`, #127):** root wrapper `apps-play-run` (runs one app play as the
unprivileged `runner-apps-play`), the `apps-janitor` timer, the hash-pinned venv `/opt/ansible-apps/venv` and runner
`host-apps`. Contract and install: `apps-runner/README.md`.
