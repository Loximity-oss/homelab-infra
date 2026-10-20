#!/usr/bin/env bash
# Install host/apps-runner on hm1-shr-cicd01 (CT 300) as root. Idempotent. Class C; run by Faris/Claude as root.
# Prerequisites (step 4.4a/4.3): users runner-apps, runner-apps-play; groups devops-locks, openbao-clients;
# /etc/openbao/ci-apps/{role_id,secret_id}. Usage, from this directory on CT 300:
#   bash install.sh                      # everything except runner registration
#   bash install.sh --register < token   # also registers the runner (Gitea repo registration token on stdin)
set -euo pipefail
export LANG=C.UTF-8 LC_ALL=C.UTF-8          # ansible refuses to start without a UTF-8 locale (pct exec has none)
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] && [ "$(hostname)" = hm1-shr-cicd01 ] || { echo "run as root on hm1-shr-cicd01"; exit 1; }
for u in runner-apps runner-apps-play; do id "$u" >/dev/null; done
V=/opt/ansible-apps/venv; C=/opt/ansible-apps/collections
say() { echo "== $*"; }

say "venv $V (hash-pinned)"
install -d -o root -g root -m 0755 /opt/ansible-apps
[ -x $V/bin/python ] || python3 -m venv $V
$V/bin/pip install -q --disable-pip-version-check --require-hashes --no-deps --only-binary=:all: -r requirements-ansible-apps.txt
chown -R root:root /opt/ansible-apps; chmod -R go-w /opt/ansible-apps
$V/bin/ansible --version | head -1

say "collections $C (sha256-verified tarballs)"
install -d -o root -g root -m 0755 $C
grep -v '^#' collections.txt | while read -r name ver sum; do
  [ -z "$name" ] && continue
  have=$($V/bin/ansible-galaxy collection list "$name" -p $C --format json 2>/dev/null | python3 -c 'import sys,json;d=json.load(sys.stdin);print(next((v["version"] for p in d.values() for k,v in p.items()),""))' 2>/dev/null || true)
  if [ "$have" = "$ver" ]; then echo "  $name $ver installed"; continue; fi
  t=$(mktemp -d); f="$t/${name/./-}-$ver.tar.gz"
  curl -sfL -o "$f" "https://galaxy.ansible.com/api/v3/plugin/ansible/content/published/collections/artifacts/${name/./-}-$ver.tar.gz"
  echo "$sum  $f" | sha256sum -c --quiet
  $V/bin/ansible-galaxy collection install --no-deps --force -p $C "$f" >/dev/null
  rm -rf "$t"; echo "  $name $ver installed"
done
chown -R root:root $C; chmod -R go-w,a+rX $C

say "wrapper, sudoers, janitor, tmpfiles"
install -o root -g root -m 0755 apps-play-run /usr/local/sbin/apps-play-run
install -o root -g root -m 0755 apps-janitor /usr/local/sbin/apps-janitor
visudo -cqf apps-play-run.sudoers
install -o root -g root -m 0440 apps-play-run.sudoers /etc/sudoers.d/apps-play-run
visudo -cq
install -o root -g root -m 0644 devops-apps.tmpfiles /etc/tmpfiles.d/devops-apps.conf
systemd-tmpfiles --create /etc/tmpfiles.d/devops-apps.conf
install -o root -g root -m 0644 apps-janitor.service apps-janitor.timer act_runner-apps.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now apps-janitor.timer >/dev/null
systemctl start apps-janitor.service

say "SSH key ci-apps (runner-apps)"
H=/var/lib/act_runner-apps
install -d -o runner-apps -g runner-apps -m 0700 $H/.ssh
[ -f $H/.ssh/ci-apps ] || sudo -u runner-apps ssh-keygen -q -t ed25519 -N '' -C ci-apps@hm1-shr-cicd01 -f $H/.ssh/ci-apps
echo "  public key (goes to homelab-infra ansible/keys/ci-apps.pub, step 4b):"; sed 's/^/  /' $H/.ssh/ci-apps.pub

say "runner act_runner-apps (label host-apps, repo homelab-apps only)"
install -d -o root -g runner-apps -m 0750 /etc/act_runner-apps
install -d -o runner-apps -g runner-apps -m 0750 $H/work
if [ ! -f /etc/act_runner-apps/config.yaml ]; then
  act_runner generate-config > /tmp/act_runner-apps.yaml
  python3 - /tmp/act_runner-apps.yaml <<'PY'
import sys, yaml
p = sys.argv[1]; c = yaml.safe_load(open(p))
c["runner"].update({"file": "/var/lib/act_runner-apps/.runner", "capacity": 1, "labels": ["host-apps:host"]})
c.setdefault("cache", {})["enabled"] = False
c.setdefault("host", {})["workdir_parent"] = "/var/lib/act_runner-apps/work"
yaml.safe_dump(c, open(p, "w"), sort_keys=False)
PY
  install -o root -g runner-apps -m 0640 /tmp/act_runner-apps.yaml /etc/act_runner-apps/config.yaml; rm -f /tmp/act_runner-apps.yaml
fi
if [ "${1:-}" = --register ] && [ ! -s $H/.runner ]; then
  read -r REG
  (cd $H && sudo -u runner-apps act_runner register --no-interactive --config /etc/act_runner-apps/config.yaml \
     --instance http://192.168.70.20:3000 --token "$REG" --name hm1-shr-cicd01-apps --labels host-apps:host >/dev/null)
  unset REG; chmod 0600 $H/.runner; echo "  registered"
fi
if [ -s $H/.runner ]; then systemctl enable --now act_runner-apps.service >/dev/null; echo "  act_runner-apps: $(systemctl is-active act_runner-apps)"
else echo "  not registered yet (run with --register)"; fi
say "done"
