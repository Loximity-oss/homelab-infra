# hm1-dmz-app01: local admin user

Decision: Faris, 2026-10-18. VM 210 (Fedora 44) has a local user `admin` in group `wheel` (sudo, password required).

- Role: `ansible/roles/base_admin_user`, played only on host `hm1-dmz-app01` (play in `ansible/playbooks/site.yml`; matches no host elsewhere).
- Password: random, generated once per instance, written to OpenBao first (create-only), never in Git or logs.
- Where: OpenBao `dmz/secret/guests/hm1-dmz-app01/admin-<instance>` (keys: username, password, vmid, created). The root break-glass password is next to it at `.../<instance>`.
- Marker on guest: `/etc/hm1/admin-user-secret-stored` (re-runs do not rotate). To rotate: Faris deletes the OpenBao entry and the marker, then re-runs the apply.
- SSH: sshd is unchanged (`PermitRootLogin prohibit-password`, `PasswordAuthentication no`). The password therefore works for console login and sudo, NOT for SSH. No SSH public key was supplied; none is installed. To allow SSH for admin, Faris supplies a public key (or decides on password SSH) and a follow-up PR adds it.
- Sudo: `/etc/sudoers.d/10-hm1-wheel` (`%wheel ALL=(ALL) ALL`), validated with visudo.
