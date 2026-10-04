#!/usr/bin/env bash
# On push to main: apply the saved PR plans for the merged PR, then configure changed hosts with Ansible.
BASE=$(cd "$(dirname "$0")/.." && pwd)
source "$BASE/scripts/ci-lib.sh"
bao_login ci-apply
export GITEA_API_TOKEN; GITEA_API_TOKEN=$(secret plan gitea_ci_token)
APPLY_TOKEN=$(secret apply pve_token_apply); PG_CONN=$(secret plan pg_conn)
bao_logout

read -r PR HEAD_SHA <<<"$(python3 "$BASE/scripts/gitea.py" pr-of-commit "$SHA")" || true
if [ -z "${PR:-}" ]; then echo "No PR for $SHA; nothing to apply."; exit 0; fi
OUT="$PLANS/$HEAD_SHA"; W=$(mktemp -d); REPORT="$W/apply.md"; : > "$REPORT"
note() { echo "$*" >> "$REPORT"; }
finish() { python3 "$BASE/scripts/gitea.py" comment "$PR" "$REPORT"; exit "$1"; }
export TF_VAR_standards_dir="$OUT/standards"
[ -d "$TF_VAR_standards_dir" ] || { note "## ❌ Apply: no saved plan for \`$HEAD_SHA\`"; finish 1; }

(cd "$BASE" && python3 scripts/changed.py "$SHA^1" "$SHA") > "$W/applied-changed.json"
mapfile -t STACKS < <(python3 -c 'import json,sys;print("\n".join(json.load(open(sys.argv[1]))["stacks"]))' "$W/applied-changed.json" | sed '/^$/d')
note "## Apply (\`${SHA:0:8}\`)"
for s in "${STACKS[@]}"; do
  n=$(stack_name "$s")
  [ -f "$OUT/$n.tfplan" ] || { note "- ❌ $n: saved plan missing"; finish 1; }
  cp "$OUT/$n.lock.hcl" "$BASE/$s/.terraform.lock.hcl"
  tf_init "$BASE/$s"
  if PROXMOX_VE_API_TOKEN="$APPLY_TOKEN" terraform -chdir="$BASE/$s" apply -input=false -no-color -lock-timeout=120s "$OUT/$n.tfplan" > "$W/$n.apply.log" 2>&1; then
    note "- ✅ $n: $(grep -E '^Apply complete' "$W/$n.apply.log" | tail -1)"
  else
    note "- ❌ **$n** apply failed"; note '```'; tail -40 "$W/$n.apply.log" >> "$REPORT"; note '```'; finish 1
  fi
done
[ ${#STACKS[@]} -eq 0 ] && note "- No Terraform stacks changed."

# ---- Ansible: changed hosts that exist, or every host when ansible/ changed
HOSTS=$(INFRA_ROOT="$BASE" python3 - "$W/applied-changed.json" <<'PY'
import json, os, sys
sys.path.insert(0, os.path.join(os.environ["INFRA_ROOT"], "scripts"))
from specs import load_specs
c = json.load(open(sys.argv[1])); have = {s["hostname"] for s in load_specs()}
print(",".join(sorted(have if c["ansible_all"] else set(c["hosts"]) & have)))
PY
)
if [ -n "$HOSTS" ]; then
  if (cd "$BASE/ansible" && INFRA_ROOT="$BASE" ansible-playbook -i ../scripts/inventory.py playbooks/site.yml --limit "$HOSTS") > "$W/ansible.log" 2>&1; then
    note "- ✅ Ansible: $HOSTS — $(grep -A20 'PLAY RECAP' "$W/ansible.log" | grep -c 'failed=0') host(s) ok"
  else
    note "- ❌ **Ansible** on $HOSTS"; note '```'; tail -40 "$W/ansible.log" >> "$REPORT"; note '```'; finish 1
  fi
else
  note "- No hosts to configure."
fi
finish 0
