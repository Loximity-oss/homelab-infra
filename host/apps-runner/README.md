# host/apps-runner — homelab-apps runner on hm1-shr-cicd01 (CT 300), Class C

Proposal `docs/proposals/homelab-apps/5-pipeline-classes.md` 5.1 and `5b-security.md`; migration step 4 (#127).
Installed by root with `install.sh` (idempotent). The apps pipeline runs as `runner-apps`; every app play runs as
`runner-apps-play` through the root wrapper below, so a play can never read the AppRole files, the Proxmox or Gitea
tokens, `$PLANS` or other runners' files.

| File | Installed to |
|---|---|
| `apps-play-run` | `/usr/local/sbin/apps-play-run` (root, 0755) |
| `apps-play-run.sudoers` | `/etc/sudoers.d/apps-play-run` (0440): `runner-apps` → `apps-play-run` only (sudo-rs: no argument wildcards; the wrapper checks the argument) |
| `apps-janitor` + `.service`/`.timer` | `/usr/local/sbin/`, `/etc/systemd/system/` (every 10 min) |
| `devops-apps.tmpfiles` | `/etc/tmpfiles.d/devops-apps.conf` (`/run/devops/apps` 0750 runner-apps:runner-apps-play) |
| `requirements-ansible-apps.txt` | venv `/opt/ansible-apps/venv` (ansible-core 2.19.9, `--require-hashes`, root-owned) |
| `collections.txt` | `/opt/ansible-apps/collections` (sha256-verified Galaxy tarballs, root-owned) |
| `act_runner-apps.service` | runner `hm1-shr-cicd01-apps`, label `host-apps`, registered to `homelab/homelab-apps` only |

## Run-dir contract (apps `ci-deploy.sh` → `apps-play-run`)

`ci-deploy.sh` (as `runner-apps`) logs in, mints the child token, takes snapshots and host locks, then per app play
creates `/run/devops/apps/<run-id>/` (`<run-id>` = digits, unique per play) and calls
`sudo /usr/local/sbin/apps-play-run <run-id>`. Everything below is owned by `runner-apps`; nothing group/other-writable.

| Path | Type / mode | Content |
|---|---|---|
| `./` | dir 0750, group `runner-apps-play` | |
| `src/apps/`, `src/infra/` | dirs, group-readable | copies of the checked-out main trees **without `.git`**, no symlinks, no special files |
| `inventory.yml` | file 0640 | inventory for this play |
| `hosts` | file 0640 | one hostname per line → `--limit`; each must have an infra guest spec in `src/infra` |
| `token` | file 0640 | this app's child token only (`APPS_BAO_TOKEN_FILE`) |
| `env` | file 0640 | `KEY=VALUE` lines, allow-list below |
| `agent.sock`, `agent.pid` | socket 0660, file | `ssh-agent -a <run dir>/agent.sock -t <timeout>` holding the `ci-apps` key |

`env` keys: `APPS_APP` (entry name), `APPS_MODE` (`check`/`apply` → `--check --diff`), `APPS_TIMEOUT` (60–14400 s,
default 3600), `APPS_WAVE`, `BAO_ZONE_MODE`, `ANSIBLE_FORCE_COLOR`, `ANSIBLE_FORKS`, `ANSIBLE_STDOUT_CALLBACK`
(`default`/`minimal`/`oneline`). `SSH_AUTH_SOCK`, `APPS_BAO_TOKEN_FILE`, `BAO_ADDR`/`VAULT_ADDR`, `BAO_CACERT`/
`VAULT_CACERT` may appear only with the value the wrapper sets anyway. Anything else is refused.

The wrapper runs `ansible-playbook -i inventory.yml --limit <hosts> -e apps_run_app=<APPS_APP> [--check --diff]
src/apps/playbooks/deploy.yml` from `src/apps` (its `ansible.cfg`) in a transient unit `apps-play-<run-id>`:
`User=runner-apps-play`, `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `PrivateDevices`,
`ProtectProc=invisible`, empty capability set, `RuntimeMaxSec=APPS_TIMEOUT`, `ReadWritePaths=<run dir>
/var/lib/runner-apps-play`, `InaccessiblePaths` for `/etc/openbao`, `/var/lib/devops`, all runner homes/configs,
Gitea and sudoers. Output: the unit's journal is printed after it ends; the exit code is the play's.

**Locks:** `/var/lib/devops/locks` is `root:devops-locks` 3770 and CT 300 has `fs.protected_regular=2`, so a runner
can only `O_CREAT`-open lock files owned by root. The janitor keeps a root-owned `host-<hostname>.lock` and `.owner`
(0664) for every guest spec on infra main; `lock_hosts` opens them without creating (`exec {fd}<file`) and rewrites
`.owner` in place. **Janitor:** removes run dirs older than 15000 s whose unit is gone (`find -xdev -depth -delete`),
kills their `ssh-agent` by recorded pid after checking owner, exe and socket path. Log: `journalctl -t apps-janitor`.

**Still open for step 5:** SSH host keys for the play user (`known_hosts` for deployment hosts) and the apps
`ansible.cfg` (`host_key_checking`, `UserKnownHostsFile`).
