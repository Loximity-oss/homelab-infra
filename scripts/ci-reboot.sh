#!/usr/bin/env bash
# reboot workflow (.gitea/workflows/reboot.yml, workflow_dispatch input `issue`; proposal 3-patching 3.4, step 9).
# One approved reboot of one patched guest. Dispatched by OpenClaw reboot_guest(issue); this script is the authority:
#   gate (newest faris comment = reboot/approve, nothing from the pipeline after it; spec; not protected; no open
#   app-verify issue in the deployment) -> policy from the catalogue (apps clone; fallback one_at_a_time) -> host locks
#   (whole deployment for pair/drain) -> partners healthy now -> snapshot pre-reboot_<ts> -> drain hook -> Proxmox
#   reboot -> health (<= reboot.timeout_s; pair: both) -> uncordon hook -> L1-L3 verify -> comment; close on success.
# Failure: stop, leave the node as it is (cordoned for k3s), keep the issue open, open app-verify: <app>/<host> (SRE).
# Every result comment carries <!-- reboot-run:<ts> -->, so one approval = one attempt.
BASE=$(cd "$(dirname "$0")/.." && pwd)
source "$BASE/scripts/ci-lib.sh"
export LC_ALL=C.UTF-8
LOCK_WORKFLOW=reboot
trap unlock_hosts EXIT
[[ "${ISSUE:-}" =~ ^[0-9]{1,6}$ ]] || { echo "ISSUE must be an issue number"; exit 2; }
bao_login ci-apply
export GITEA_API_TOKEN; GITEA_API_TOKEN=$(secret plan gitea_ci_token)
APPLY_TOKEN=$(secret apply pve_token_apply); PLAN_PVE_TOKEN=$(secret plan pve_token_plan)
APPS_ISSUE_TOKEN=$(secret apply gitea_apps_dispatch_token 2>/dev/null) || APPS_ISSUE_TOKEN=""   # ci-apps bot: app-verify issues
bao_logout

STAMP=$(date +%Y%m%d_%H%M); W=$(mktemp -d); REPORT="$W/reboot.md"; PLAN="$W/plan.json"; : > "$REPORT"
note() { echo "$*" >> "$REPORT"; }
RG() { INFRA_ROOT="$BASE" python3 "$BASE/scripts/reboot_guest.py" "$@"; }
P() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1])).get(sys.argv[2], ""); print(",".join(v) if isinstance(v, list) else v)' "$PLAN" "$1"; }
block() { note '```'; tr -d '`' < "$1" | tail -"${2:-30}" >> "$REPORT"; note '```'; }
post() {  # rc: comment the result on the issue (with the run marker), close it on success
  note ""; note "<!-- reboot-run:$STAMP -->"
  python3 "$BASE/scripts/gitea.py" comment "$ISSUE" "$REPORT" || true
  if [ "$1" -eq 0 ]; then
    python3 "$BASE/scripts/gitea.py" issue-close "patch: reboot $HOST" "Closed by the reboot workflow (run $STAMP)." || true
  fi
  unlock_hosts; exit "$1"
}

clone homelab/homelab-standards "$W/standards" "$GITEA_API_TOKEN"
APPS="$W/apps"; clone homelab/homelab-apps "$APPS" "$GITEA_API_TOKEN" > "$W/apps-clone.log" 2>&1 || APPS="-"

# ---- 1. gate
rc=0; RG gate "$ISSUE" "$W/standards" "$APPS" "$PLAN" > "$W/gate.txt" 2>&1 || rc=$?
cat "$W/gate.txt"
if [ ! -f "$PLAN.valid" ]; then echo "issue #$ISSUE is not a reboot issue (rc $rc): nothing done, no comment"; exit 1; fi
HOST=$(head -1 "$PLAN.valid")
note "## Reboot \`$HOST\` (run $STAMP)"
if [ $rc -ne 0 ]; then
  note "- ⛔ **Refused** (nothing done):"; block "$W/gate.txt" 20
  note "Faris: if this should still happen, fix the reason and answer \`reboot\` again."; post 1
fi
APP=$(P app); POLICY=$(P policy)
note "- ✅ Approved by faris at $(P approved_at); policy \`$POLICY\`${APP:+ (deployment \`$(P deployment)\`)}"
python3 -c 'import json,sys; [print(w) for w in json.load(open(sys.argv[1])).get("warnings", [])]' "$PLAN" | while read -r w; do note "  - ⚠️ $w"; done

app_verify() {  # why: open app-verify: <app>/<host> in homelab-apps (SRE triages); note if that is impossible
  [ -n "$APP" ] || return 0
  { echo "**Reboot of \`$HOST\` failed** (infra reboot workflow, run $STAMP, infra issue #$ISSUE): $1."; echo
    echo "The node was left as it is (k3s: still cordoned). Snapshot \`pre-reboot_$STAMP\` (7 days)."
    echo "No other host of \`$(P deployment)\` is rebooted while this issue is open."; } > "$W/av.md"
  if [ -n "$APPS_ISSUE_TOKEN" ] && REPO=homelab/homelab-apps GITEA_API_TOKEN="$APPS_ISSUE_TOKEN" \
       python3 "$BASE/scripts/gitea.py" issue-upsert "app-verify: $APP/$HOST" app-verify "$W/av.md" >/dev/null 2>&1; then
    note "- 📋 Opened \`app-verify: $APP/$HOST\` in homelab-apps"
  else
    note "- ⚠️ Could not open \`app-verify: $APP/$HOST\` in homelab-apps: **SRE, open it by hand**"
  fi
}
fail() { note "- ❌ **$1**"; [ -n "${2:-}" ] && block "$2" "${3:-30}"; app_verify "$1"; note "The issue stays open (SRE triages)."; post 1; }

# ---- 2. locks (whole deployment for pair/drain)
if lock_hosts "$(P lock)"; then note "- 🔐 Host locks: $(locked_hosts)"
else
  note "- ⛔ **Host locks** not acquired, nothing done (issue \`pipeline: lock wait timeout <host>\` names the holder)"
  for n in "${LOCK_NOTES[@]}"; do note "  - $n"; done; post 1
fi
# ---- 3. partners healthy now (pair, drain)
if [ "$POLICY" != "one_at_a_time" ]; then
  for o in $(P others | tr ',' ' '); do
    if ! RG health "$PLAN" "$o" > "$W/pre-$o.txt" 2>&1; then
      note "- ⛔ **Refused**: \`$o\` (same deployment) is not healthy now, nothing done"; block "$W/pre-$o.txt" 10; post 1
    fi
  done
  note "- ✅ Other hosts healthy before: $(P others)"
fi
# ---- 4. snapshot
if PROXMOX_VE_API_TOKEN="$APPLY_TOKEN" INFRA_ROOT="$BASE" python3 "$BASE/scripts/snapshot.py" take --name "pre-reboot_$STAMP" --hosts "$HOST" > "$W/snap.txt" 2>&1 \
   && grep -q "^$HOST ok" "$W/snap.txt"; then
  note "- 📸 Snapshot \`pre-reboot_$STAMP\` (kept 7 days)"
else
  note "- ⛔ **No snapshot**, so no reboot"; block "$W/snap.txt" 10; post 1
fi
# ---- 5. drain hook, reboot, health, uncordon hook
RG hook "$PLAN" before > "$W/before.txt" 2>&1 || fail "drain (before) failed on $(P hook_host)" "$W/before.txt"
[ "$POLICY" = drain ] && note "- ✅ Drained: $(tail -1 "$W/before.txt" | tr -d '`' | cut -c1-200)"
PROXMOX_VE_API_TOKEN="$APPLY_TOKEN" RG pve-reboot "$PLAN" > "$W/reboot.txt" 2>&1 || fail "reboot failed" "$W/reboot.txt" 10
note "- 🔁 $(tail -1 "$W/reboot.txt" | tr -d '`')"
RG wait-health "$PLAN" "$HOST" > "$W/health.txt" 2>&1 || fail "\`$HOST\` not healthy within $(P timeout_s) s after the reboot" "$W/health.txt" 10
note "- ✅ Health: $HOST${APP:+ (catalogue \`$APP\`)}"
if [ "$POLICY" = pair ]; then
  for o in $(P others | tr ',' ' '); do
    RG wait-health "$PLAN" "$o" > "$W/health-$o.txt" 2>&1 || fail "partner \`$o\` not healthy after the reboot" "$W/health-$o.txt" 10
  done
  note "- ✅ Partner(s) healthy after: $(P others)"
fi
RG hook "$PLAN" after > "$W/after.txt" 2>&1 || fail "uncordon (after) failed on $(P hook_host)" "$W/after.txt"
[ "$POLICY" = drain ] && note "- ✅ Uncordoned"
# ---- 6. L1-L3
vok=0; PROXMOX_VE_API_TOKEN="$PLAN_PVE_TOKEN" INFRA_ROOT="$BASE" python3 "$BASE/scripts/verify.py" --standards "$W/standards" \
  --hosts "$HOST" --out "$W/verify" > "$W/verify.log" 2>&1 || vok=$?
note ""; note "### Tests"; [ -f "$W/verify/verify.md" ] && cat "$W/verify/verify.md" >> "$REPORT"
[ $vok -eq 0 ] || fail "L1-L3 tests failed after the reboot" "$W/verify.log" 20
note ""; note "**✅ Reboot done.**"
post 0
