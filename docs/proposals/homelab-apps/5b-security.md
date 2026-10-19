# 5b. Security: lint gate, ansible-core, privilege separation

(Part of section 5.1; split out to stay under the `read_file` limit.)

**PR lint gate** over **everything under `catalog/<app>/`** — role tasks, handlers, defaults, vars, every `.j2`
anywhere in the entry (incl. `files/**`), and any file used as a `template` src — before render. A lint failure turns
the `pr / plan` check **red**, so the PR cannot merge and the render diff is skipped (reviewer 12, 3.2 notes 1, f):
- **Jinja is checked on the parsed AST** (`jinja2.Environment().parse()` of every expression and template), not on
  raw text, because names can be built at render time (reviewer 3.2-2, 3.3):
  - the `attr` filter is rejected;
  - an `attribute=` argument (on `map`, `selectattr`, `rejectattr`, `sort`, `groupby`, `sum`, `min`, `max`,
    `unique`, …) must be a constant string that does not start with `_`;
  - `Getattr` names starting with `_` or equal to `mro` are rejected. The `_` rule applies to **attribute and subscript
    names only**, never to variable names (`Name` nodes): `_k3s_swap.stdout` passes, `x._y` fails (test; cfg 3.6-3);
  - `Getitem` subscripts must be constants not starting with `_` and not `mro`; the only variable subscript allowed is
    `hostvars[<var>]` (HostVars returns Undefined, never falls back to attributes). `groups[...]` takes a constant only
    (`role_<code>`), because a dict `getitem` falls back to `getattr` (reviewer 3.5-1);
  - **filters and tests are allow-listed** (`policy/jinja-allow.yaml`, Class C; seeded from the roles' real use:
    filters `b64decode`, `trim`, `length`, `default`, `combine`, `to_json`, `sort`, `map`, `select`, `index`; tests
    `search`, `defined`) (cfg 3.6-4); anything else fails. `meta: flush_handlers` is accepted (test; cfg 3.6-8). Filter names
    passed as strings (`map('attr', …)`, `select`/`reject`/`selectattr`/`rejectattr` with a non-allow-listed name) and
    `extract` with a non-constant or `_`-leading argument are rejected (reviewer 3.5-2);
  - every expression context is parsed, not only `{{ }}`: `when`, `changed_when`, `failed_when`, `until`,
    `assert.that`, `loop`, `with_*`, and every templated module argument (reviewer 3.5-5);
  - every YAML scalar under `catalog/<app>/role/**` (defaults, vars, task args) containing `__` or equal to `mro` is
    rejected, as for deployment values (reviewer 3.5-4);
  - any constant string containing `__` or equal to `mro` is rejected (catches `'\x5f\x5f'`, `'_'*2` results stay
    non-constant and fall under the rules above);
  - unit tests, one per bypass: `attr('_'~'_init_'~'_')`, `'_'*2`, `'%c%c'|format(95,95)`, `'\x5f\x5f'`,
    `map(attribute=x)`, `foo[var]`, a deployment var holding `__globals__`;
- rejects `local_action`, `connection: local`, `add_host`, `fetch`, `synchronize`, `script`, `raw` on localhost;
- `delegate_to` only to **another host of the same deployment**: a host name, an expression over
  `groups['role_<code>']`, or a **bare variable** (e.g. `svc_k3s_init_host`) — a variable is accepted because the
  deploy guard checks every resolved target at run time (same deployment, not protected); never `localhost`,
  `127.0.0.1` or the controller; unit test (cfg 3.2-B1, 3.5-1);
- `check_mode: false` only on tasks that also have `changed_when: false` and use a read-only module (`command`,
  `shell`, `slurp`, `stat`, `uri` with GET) (cfg 3.2-B2);
- the banned names (`zone`, `vmid`, `role`, `type`, `os`, `bao_*`, `deploy_*`) may not be set by `set_fact`/`register`, any task/block/`include_role` `vars:`, role params, or an `include_vars` file (reviewer 3.2-a, 3.3);
- rejects every lookup and query in any spelling — `lookup(...)`, `query(...)`, `q(...)`, FQCN
  (`ansible.builtin.*`, `community.*`) and `with_<lookup>` loops — except the plain loop forms `with_items`,
  `with_dict`, `with_list`, `loop`;
- `src:` of `copy`/`template`/`unarchive`/`assemble`, `file:`/free-form of `include_vars`, and the target of
  `include_tasks`/`import_tasks` must **resolve statically** under `catalog/<app>/` (absolute paths, escapes and
  unresolvable templated paths rejected; `{{ role_path }}/../files` allowed); `remote_src: true` exempt (cfg R3,
  reviewer 3.2-b);
- any `ansible_*` key in role `vars/`/`defaults/`;
- **no exceptions and no per-role allow-lists**: the only controller-side action a role may use is `include_role:
  bao_create_secret`.

**ansible-core ≥ 2.19** (no re-templating of strings built at runtime) in a **separate, root-owned, hash-pinned venv**
`/opt/ansible-apps/venv` (`ansible-core==2.19.x`, `pip install --require-hashes`), invoked by absolute path, shared
read-only by `runner`, `runner-apps` and `runner-apps-play`. The infra venv is **not** changed by this migration
(2.19 changes templating for infra applies; that would be its own infra PR). `deploy.yml` `pre_tasks` and the `bwrap`
render assert the version (reviewer 3.5-3, N1, infra 3.6-5).

**Privilege separation — the lint is defence in depth, not the only boundary** (Claude, rev 3.6; answers the class of
findings in reviewer 3.2-2, 3.3, 3.5):
- **Deploy:** `ci-deploy.sh` (main's code, as `runner-apps`) logs in to OpenBao, mints the per-app child tokens, takes
  the `pre-deploy_*` snapshots, takes the host locks (holding the fds) and fetches inventory **first**. Then, **once per
  app play** (reviewer R3.6-1), it writes `/run/devops/apps/<run-id>/` (`0750 runner-apps:runner-apps-play`: one `0640`
  token file for that app only, an `env` file, an `ssh-agent` socket holding the `ci-apps` key — the play user never
  reads the key file) and calls `sudo -u runner-apps-play /usr/local/sbin/apps-play-run <run-id>` (infra 3.6-1).
- **Wrapper** `apps-play-run` (root-owned, Class C, in infra `host/`): validates `<run-id>` (`^[0-9]+$`), reads the run
  dir, pins `playbooks/deploy.yml`, the inventory and `--limit` to that deployment's hosts, and runs
  `/opt/ansible-apps/venv/bin/ansible-playbook` under `systemd-run --collect --wait -p RuntimeMaxSec=<job timeout>`, so
  the child can never outlive the job and its locks (infra 3.6-3). Sudoers: `runner-apps ALL=(runner-apps-play)
  NOPASSWD: /usr/local/sbin/apps-play-run *` with `env_reset`, `!setenv`, `secure_path`; nothing else. The run dir is
  removed by a trap and in `finish()`; locks are released only after the child has exited.
- **`runner-apps-play`** owns nothing `runner-apps` later reads or runs: read-only checkout, own `0700` HOME, `TMPDIR`,
  `ANSIBLE_LOCAL_TEMP`, `ANSIBLE_REMOTE_TEMP` and ControlPath; not in `devops-locks` (N2, infra 3.6-2, 3.6-3). It can
  read none of `/etc/openbao/*`, the Proxmox token, the Gitea tokens, `$PLANS`, or `runner-apply` files.
  An escape in one app's play gains that app's create-only token and **SSH to every deployment host via the shared
  `ci-apps` key** — the second part is an **accepted risk** for now; per-app SSH keys are Q13 (later).
- **PR render (host-plan):** `bwrap --unshare-all --die-with-parent --new-session --clearenv` (no `--share-net`),
  `/usr` read-only plus the usual symlinks, `--proc /proc --dev /dev --tmpfs /tmp`, read-only binds of the PR tree, the
  infra and apps clones and the installed collections (never downloaded at render time), from `/etc` only
  `resolv.conf` and `ssl`, one writable bind for the output dir; it asserts the ansible-core version (N1, N3, infra
  3.6-4). Prerequisite **verified live 2026-10-20**: CT 300 has `nesting=1`, `bubblewrap 0.11.1`, and the full flag set
  runs as `runner`, so no CT 300 change is needed; fallback if that ever changes: a `systemd-run` sandbox
  (`PrivateNetwork=yes`, `ProtectSystem=strict`, `InaccessiblePaths=/etc/openbao`). An escape during render reaches
  nothing.
- **File modes** (step 4 table; infra 3.6-2): `/etc/openbao` not world-traversable; `/etc/openbao/ci-apps/` `0700`,
  `role_id`/`secret_id` `0600 runner-apps`; the OpenBao CA copied to `/etc/ssl/devops/openbao-ca.crt` for the play user;
  `kernel.yama.ptrace_scope` ≥ 1 (live: 1), so the play user cannot read `/proc/<runner-apps pid>/environ`.
- **Child tokens** (infra 3.6-6): TTL = the wave's expected length (one token per wave), create-only on that app's path
  until it expires; the token role has no `bound_cidrs` that would break `bao_create_secret` on CT 300.
- Unit/integration tests in step 5 prove both: a role that tries to read `/etc/openbao/ci-apps/secret_id` or the
  Proxmox token fails with permission denied at deploy, and a render that tries `curl` fails with no network.

