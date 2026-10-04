#!/usr/bin/env bash
# PR pipeline (pull_request_target): this script and policy come from main ($BASE); the PR's code is only
# linted and planned (read-only token), never executed. Saved plans are applied by ci-apply.sh after merge.
BASE=$(cd "$(dirname "$0")/.." && pwd); WORK=$(dirname "$BASE")
source "$BASE/scripts/ci-lib.sh"

bao_login ci-plan
export GITEA_API_TOKEN; GITEA_API_TOKEN=$(secret plan gitea_ci_token)
PLAN_TOKEN=$(secret plan pve_token_plan); PG_CONN=$(secret plan pg_conn)
bao_logout
find "$PLANS" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} + 2>/dev/null || true

OUT="$PLANS/$HEAD_SHA"; rm -rf "$OUT"; mkdir -p "$OUT"
clone homelab/homelab-standards "$OUT/standards" "$GITEA_API_TOKEN"
export TF_VAR_standards_dir="$OUT/standards"

git -C "$BASE" fetch -q origin "refs/pull/$PR/head"
[ "$(git -C "$BASE" rev-parse FETCH_HEAD)" = "$HEAD_SHA" ] || { echo "PR head moved; a newer run will handle it"; exit 0; }
HEAD_DIR="$WORK/head"; rm -rf "$HEAD_DIR"; git -C "$BASE" worktree add -q -f --detach "$HEAD_DIR" "$HEAD_SHA"
MAIN=$(git -C "$BASE" rev-parse origin/main)
REPORT="$OUT/report.md"; : > "$REPORT"
note() { echo "$*" >> "$REPORT"; }

# Branch behind main -> update it (a new run follows on the updated head)
if ! git -C "$BASE" merge-base --is-ancestor "$MAIN" "$HEAD_SHA"; then
  python3 "$BASE/scripts/gitea.py" update-branch "$PR"
  echo "Branch was behind \`main\`; merged \`main\` into it. A new run will plan the updated branch." > "$REPORT"
  python3 "$BASE/scripts/gitea.py" comment "$PR" "$REPORT"; exit 0
fi

# ---- lint (all from main or standards, run against the PR tree)
fail=0
run_check() { local name=$1; shift; local log="$OUT/$name.log"
  if "$@" > "$log" 2>&1; then note "- ✅ $name"; else fail=1; note "- ❌ **$name**"; note '```'; head -40 "$log" >> "$REPORT"; note '```'; fi; }
note "### Checks"
run_check standards python3 "$OUT/standards/lint/naming.py" --infra "$HEAD_DIR" --branch "$HEAD_REF" --title "$TITLE"
run_check tf-safety bash "$BASE/scripts/tf-safety.sh" "$HEAD_DIR"
run_check terraform-fmt terraform fmt -check -recursive -diff "$HEAD_DIR/terraform"
run_check yamllint yamllint -d relaxed "$HEAD_DIR/terraform/stacks" "$HEAD_DIR/ansible" "$HEAD_DIR/.gitea"
run_check gitleaks gitleaks dir "$HEAD_DIR" --no-banner --redact

# ---- plan changed stacks
(cd "$HEAD_DIR" && python3 "$BASE/scripts/changed.py" "$MAIN" "$HEAD_SHA") > "$OUT/changed.json"
mapfile -t STACKS < <(python3 -c 'import json,sys;print("\n".join(json.load(open(sys.argv[1]))["stacks"]))' "$OUT/changed.json" | sed '/^$/d')
note ""; note "### Plans"
[ ${#STACKS[@]} -eq 0 ] && note "No Terraform stacks changed."
if [ $fail -eq 0 ]; then
  for s in "${STACKS[@]}"; do
    n=$(stack_name "$s"); d="$HEAD_DIR/$s"
    if ! { tf_init "$d" && terraform -chdir="$d" validate -no-color; } > "$OUT/$n.init.log" 2>&1; then
      fail=1; note "- ❌ **$n** init/validate"; note '```'; tail -30 "$OUT/$n.init.log" >> "$REPORT"; note '```'; continue; fi
    set +e
    PROXMOX_VE_API_TOKEN="$PLAN_TOKEN" terraform -chdir="$d" plan -input=false -no-color -detailed-exitcode -lock-timeout=120s -out="$OUT/$n.tfplan" > "$OUT/$n.plan.log" 2>&1
    rc=$?; set -e
    if [ $rc -eq 1 ]; then fail=1; note "- ❌ **$n** plan failed"; note '```'; tail -40 "$OUT/$n.plan.log" >> "$REPORT"; note '```'; continue; fi
    terraform -chdir="$d" show -json "$OUT/$n.tfplan" > "$OUT/$n.plan.json"
    cp "$d/.terraform.lock.hcl" "$OUT/$n.lock.hcl"
    note "<details><summary><b>$n</b>: $(grep -E '^(Plan:|No changes|Changes to Outputs)' "$OUT/$n.plan.log" | head -1 || true)</summary>"; note ""; note '```'
    { sed -n '/Terraform will perform/,$p' "$OUT/$n.plan.log" | grep -v '^Saved the plan' | head -150 || true; } >> "$REPORT"; note '```'; note "</details>"
  done
fi

# ---- classify + gate
if [ $fail -ne 0 ]; then
  { echo "## ❌ Checks failed"; echo; cat "$REPORT"; } > "$OUT/comment.md"
  python3 "$BASE/scripts/gitea.py" comment "$PR" "$OUT/comment.md"; exit 1
fi
python3 "$BASE/policy/classify.py" --changed "$OUT/changed.json" --plans "$OUT" --protected "$OUT/standards/protected.yaml" > "$OUT/class.json"
CLASS=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["class"])' "$OUT/class.json")
WIP=""; case "$TITLE" in WIP*) WIP=wip;; esac
python3 "$BASE/scripts/gitea.py" labels "$PR" "class/$CLASS${WIP:+,wip}"
case "$CLASS$WIP" in
  A)  GATE="Class A — merging automatically when checks pass."; python3 "$BASE/scripts/gitea.py" automerge "$PR" "$HEAD_SHA" ;;
  B)  GATE="Class B — needs approval from the Reviewer (\`oc-rev\`)."; python3 "$BASE/scripts/gitea.py" review-request "$PR" oc-rev || true ;;
  C)  GATE="Class C — needs Faris's approval."; python3 "$BASE/scripts/gitea.py" review-request "$PR" faris || true ;;
  *wip) GATE="WIP — planned only; remove \`WIP\` from the title to proceed." ;;
esac
python3 - "$OUT/class.json" "$REPORT" "$GATE" > "$OUT/comment.md" <<'PY'
import json, sys
c = json.load(open(sys.argv[1])); s = c["summary"]
print(f"## Class {c['class']}\n\n{sys.argv[3]}\n")
print(f"create {s['create']} · update {s['update']} · destroy {s['delete']} · replace {s['replace']} · import {s['import']}\n")
print("**Why:** " + "; ".join(c["reasons"][:10]) + "\n")
print(open(sys.argv[2]).read())
PY
python3 "$BASE/scripts/gitea.py" comment "$PR" "$OUT/comment.md"
