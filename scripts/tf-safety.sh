#!/usr/bin/env bash
# Terraform constructs that could run code or exfiltrate on the CI host are not allowed in PRs.
set -euo pipefail
cd "${1:-.}"
bad=$(grep -rnE --include='*.tf' 'provisioner[[:space:]]+"|"local-exec"|"remote-exec"|data[[:space:]]+"external"|hashicorp/external|hashicorp/http|data[[:space:]]+"http"|filebase64|templatefile\(.*/(root|var|etc|home)/|file\("/' terraform || true)
if [ -n "$bad" ]; then echo "tf-safety: forbidden constructs:"; echo "$bad"; exit 1; fi
echo "tf-safety: ok"
