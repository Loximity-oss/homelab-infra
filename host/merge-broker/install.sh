#!/usr/bin/env bash
# Install host/merge-broker on hm1-shr-cicd01 (CT 300) as root. Idempotent. Class C (infra #135).
# Prerequisites: user runner (host-plan), /etc/openbao/ci-plan/{role_id,secret_id} readable by runner, bao in /usr/local/bin.
# Usage, from this directory on CT 300:  bash install.sh [--dry-run-only]
#   --dry-run-only   install, run once with --dry-run, and leave the timer DISABLED (first install / proofs)
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 LC_ALL=C.UTF-8
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] && [ "$(hostname)" = hm1-shr-cicd01 ] || { echo "run as root on hm1-shr-cicd01"; exit 1; }
id runner >/dev/null
sudo -u runner test -r /etc/openbao/ci-plan/secret_id || { echo "runner cannot read /etc/openbao/ci-plan"; exit 1; }
install -o root -g root -m 0755 merge-broker /usr/local/sbin/merge-broker
install -o root -g root -m 0644 merge-broker.service merge-broker.timer /etc/systemd/system/
install -d -o runner -g runner -m 0750 /var/lib/devops/apps-plans    # apps ci-pr.sh writes <sha>/class.json here
systemctl daemon-reload
echo "== dry run"
systemd-run --quiet --wait --pipe -p User=runner -p Group=runner -p StateDirectory=merge-broker \
  -E HOME=/var/lib/merge-broker /usr/local/sbin/merge-broker --dry-run
if [ "${1:-}" = --dry-run-only ]; then
  systemctl disable --now merge-broker.timer >/dev/null 2>&1 || true; echo "timer left disabled"
else
  systemctl enable --now merge-broker.timer >/dev/null; systemctl list-timers merge-broker.timer --no-pager
fi
