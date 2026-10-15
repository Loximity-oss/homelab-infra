# VM templates: lifecycle (owner: Infrastructure Engineer)

Decided by Faris on 2026-10-15 (homelab-standards PR #17, `naming/vmid-ranges.yaml` `template_lifecycle`; tag value
`mgd-tpl` per standards #18). In-guest requirements (packages, root SSH, serial console, hygiene) are in
`docs/apps/vm-templates.md` (Configuration Engineer).

## Rules

- Templates live in 9000-9099 (Linux). The builder may also refresh 9000. Windows 9100-9199 stays manual.
- **Clone-only for the apply token** (decision 32 D5): `scripts/template-guard.py` blocks every guest apply if a
  template is more than VM.Audit + VM.Clone for it. Guest stacks never manage templates.
- **Built only by the `template` workflow** with `svc-tpl-build@pve!pipeline`. That token has role DevOpsTemplateBuild
  through per-VMID ACLs on 9000-9099 and scratch 9990-9999, never through a pool. On storage it has `dir-import01`
  (import, backup) and `lvmt-nvme01`. On the network it has SDN.Use on vmbr1 only. It has nothing on `/`, `/nodes`,
  `/access`, `/pool`, guests, protected VMIDs or vmbr0. `scripts/template-builder-guard.py` checks this before every
  build.
- Nobody runs a shell on the PVE host for templates. Proxmox API only: upload + `import-from`, no `download-url`.
- One spec per template: `terraform/templates/hm1/<vmid>-<name>.yaml`. The name follows naming-conventions 6.4
  (`tpl-<os><version>-<variant>`).
- Pipeline-built templates are tagged `os-<os>;mgd-tpl;owner-infra`.

## Gates

| Change | Class |
|---|---|
| Minor refresh: an existing spec where only `source.url` (same host), `source.sha256`, `source.version`, `source.signature.url`, `source.nixpkgs_rev` change | B (Reviewer) |
| New spec, deleted spec (retires the template), rename, any other key (settings, seal, provision, refresh, signature key), vendor keys, nixos config | C (Faris) |
| Workflow, scripts, classifier, range, identity | C (Faris) |

Computed by `policy/classify.py` (`template_change`). It fails closed to C.

## Spec

```yaml
name: tpl-fedora44-base
vmid: 9002
os: fedora44                      # naming/tags.yaml os
description: ...
source:
  kind: image                     # image | nixos-build
  url: https://...qcow2           # pinned, no placeholders, no /latest/
  version: "44-1.7"
  sha256: <64 hex>                # from the vendor's signed checksum file
  signature:                      # optional: {url, type: detached|clearsigned-checksum, key: terraform/templates/keys/..., fingerprint}
  # nixos-build: config, nixpkgs_rev (40-hex commit)
settings: {}                      # only cores, memory_mb, datastore, cpu; disk is fixed at 8G
seal: ssh                         # ssh: one boot in stg to run provision + hygiene; none: image used as is
seal_user: fedora                 # optional: image default user with sudo (cloud images disable root login)
provision: [ ... ]                # shell steps, run only inside the build VM
refresh: monthly                  # weekly | monthly | quarterly | release
refresh_check: {checksum_url: https://..., match: <regex>}   # optional weekly upstream check
```

`scripts/template-build.py check` validates every spec in the PR gate.

## Build (merge to main → `template` workflow, runner `host-template`)

1. The builder guard passes and the template lock is taken exclusively. Guest applies take it shared, so no clone
   happens while a template is being replaced.
2. Get the image.
   - `image`: download on the runner, check the sha256 against the spec, check the vendor signature if one is set,
     then upload to `dir-import01` with the checksum.
   - `nixos-build`: a throwaway VM 9990 (full clone of 9000) in stg builds `config.system.build.images.qemu` with
     nixpkgs' own image builder at the pinned `nixpkgs_rev`. The qcow2 is copied back and the VM is destroyed.
     nix-community/nixos-generators is archived (2026-01-30) and not used.
3. Create candidate 9991 with `import-from` and the 9000 reference settings. The disk is 8G.
4. `seal: ssh`: one boot on stg VLAN 60 at the `template-build` reserved IP with an ephemeral key. The build runs the
   provision steps, then the hygiene steps:
   - `cloud-init clean`
   - empty machine-id
   - remove SSH host keys and authorized_keys

   Then a clean poweroff, and the cloud-init user/IP is removed. After that the candidate becomes a template.
5. Verify on a throwaway clone 9992. It logs in as root with a key, like real guests, and its disk is 4G larger. Checks:
   - cloud-init done
   - python3
   - sshd and qemu-guest-agent active
   - the agent reports the IP
   - the disk grew
   - machine-id present
   - serial console

   If anything fails, the live template stays untouched.
6. Replace the live template.
   - Back it up (vzdump to `dir-import01`), then delete it without `purge`, because purge drops the per-VMID ACLs.
   - Full-clone the candidate to the target VMID and convert it to a template. If that fails, the old template is
     restored automatically.
   - Set the description (spec, git sha, image sha256, date) and the tags `os-<os>;mgd-tpl;owner-infra`. If PVE refuses
     the registered tags, the report prints the root one-off.
7. Prune to the last 2 imports and 2 backups per template. Scratch is always cleaned up.

The build result is posted as a comment on the PR. A deleted spec retires its template (backup, then delete).

## Known weaker points

- **No vendor GPG check yet.** The Arch and Fedora pins are sha256-only, fetched over vendor TLS. The values were
  researched by the Configuration Engineer via WebFetch, and the builder re-verifies them: the download must match the
  pin. Adding the vendor keys (`terraform/templates/keys/`) and `signature` blocks is a Class C follow-up, once someone
  has verified the fingerprints out of band.
- **NixOS first build** uses `images.qemu` from the pinned nixpkgs, and `nix-bin` on Ubuntu. Expect iteration on the
  first run.

## Refresh, rollback, audit

- **Refresh**: a weekly check (Mon 04:00 MYT) compares the upstream checksum file with the pinned sha256 and opens
  `template refresh: <name>`. The Infrastructure Engineer then opens a minor-refresh PR (Class B). Existing guests are
  full clones and are never affected.
- **Rollback**: a revert PR rebuilds from the previous pin, reusing the cached import if it is still kept. If upstream
  is gone, restore the pre-replace vzdump in `dir-import01`.
- **Audit**: the PR records who, why and the pin. The PR comment records each step. PVE tasks run as
  `svc-tpl-build@pve`. The template description records spec path, git sha, image sha256 and date.
- **Rebuild without a change**: dispatch the `template` workflow with `spec`. Only people with repo write access can
  do this; agents have no tool for it.
