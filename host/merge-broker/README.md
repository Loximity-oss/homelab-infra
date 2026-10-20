# merge-broker (CT 300, root-installed, runs as `runner`)

The only automated merger of `homelab-infra` and `homelab-apps` (infra #135, Faris 2026-10-20). Agents approve; they
never merge. Branch protection merge whitelist = `ci-merger`, `faris`.

| Class | Merges when (all on the current head sha) |
|---|---|
| A | `pr / plan (pull_request_target)` green (Actions-posted) and `class.json` = A |
| B | same + latest review by `oc-rev` or `faris` = APPROVED, not stale or dismissed |
| C | same + latest review by `faris` = APPROVED, not stale or dismissed |

Never: WIP/draft, a non-dismissed REQUEST_CHANGES, no `class.json`, the author's own approval, a status posted with a token.
Class comes only from `<plans>/<sha>/class.json` (main's `ci-pr.sh` + main's classifier; infra
`/var/lib/devops/plans/<sha>/`, apps `/var/lib/devops/plans/apps-<sha>/`). Labels are display only.

| File | Installed as |
|---|---|
| `merge-broker` | `/usr/local/sbin/merge-broker` (python3 -I, stdlib) |
| `merge-broker.{service,timer}` | `/etc/systemd/system/`, every 2 min |

Install / update: `bash install.sh` (root, CT 300); first time `bash install.sh --dry-run-only`.
Log: `journalctl -t merge-broker`. `systemctl start merge-broker` runs it for real; for a dry run use the `systemd-run` line in `install.sh`.
Tests: `python3 scripts/tests/test-merge-broker.py`.
Prerequisite for soundness in infra: #126 (agents must not write `.gitea/*`; a PR-branch workflow on `host-plan`
runs as `runner` and could forge `class.json`).
