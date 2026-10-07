#!/usr/bin/env bash
# Nightly: plan every stack with the read-only token; open/append a `drift` issue per drifted stack.
BASE=$(cd "$(dirname "$0")/.." && pwd); WORK=$(dirname "$BASE")
source "$BASE/scripts/ci-lib.sh"
bao_login ci-plan
export GITEA_API_TOKEN; GITEA_API_TOKEN=$(secret plan gitea_ci_token)
PLAN_TOKEN=$(secret plan pve_token_plan); PG_CONN=$(secret plan pg_conn)
bao_logout
rm -rf "$WORK/standards"; clone homelab/homelab-standards "$WORK/standards" "$GITEA_API_TOKEN"
export TF_VAR_standards_dir="$WORK/standards"
rc_all=0
for s in $(all_stacks "$BASE"); do
  n=$(stack_name "$s"); [ -n "$(ls "$BASE/$s/guests"/*.yaml 2>/dev/null)" ] || { echo "$n: no guests"; continue; }
  tf_init "$BASE/$s"
  set +e; PROXMOX_VE_API_TOKEN="$PLAN_TOKEN" terraform -chdir="$BASE/$s" plan -input=false -no-color -detailed-exitcode -lock=false > "$WORK/$n.drift.log" 2>&1; rc=$?; set -e
  case $rc in
    0) echo "$n: in sync" ;;
    2) { echo "Nightly plan of \`$n\` differs from Git. Codify the change via PR or revert it."; echo; echo '```'; sed -n '/Terraform will perform/,$p' "$WORK/$n.drift.log" | head -120; echo '```'; } > "$WORK/$n.md"
       python3 "$BASE/scripts/gitea.py" issue-upsert "drift: $n" drift "$WORK/$n.md"; echo "$n: DRIFT" ;;
    *) echo "$n: plan error"; tail -20 "$WORK/$n.drift.log"; rc_all=1 ;;
  esac
done
exit $rc_all
