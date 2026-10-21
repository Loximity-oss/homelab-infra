#!/usr/bin/env bash
# Ring-1 patching, pipeline side (docs/patching-pipeline.md; Ansible side: docs/apps/patching.md). Modes:
#   scan     every 6 h: patch-scan.yml on all pipeline guests except ring0 (read-only); ring-1 hosts with Sev1/2 ->
#            pre-patch_ snapshot -> patch.yml -e patch_max_sev=2; reboot issues for Faris.
#   bundle   25th: fresh scan; Sev3/4 per ring-1 host -> PR patches/hm1/<YYYY-MM>.{md,json} (Class C, Faris approves).
#   monthly  last Saturday 02:00 MYT: only a bundle PR merged with Faris's approval; snapshot -> patch.yml
#            -e patch_max_sev=4 -e @allow.json (patch_bundle_allow = the approved Sev3/4 packages per host).
# Never reboots anything. Protected VMIDs / templates / ring0 are never selected (patch_plan.py, snapshot.py, role).
# Host locks (step 4b): the read-only scan takes none; patch_run() locks its whole host set in ONE lock_hosts call
# before the snapshot and releases it when the patch run ends.
# Results kept on the runner in /var/lib/devops/patch/<stamp>-<mode>/ (35 days; 'latest' = newest).
# Exit 1 on any failure -> pipeline.failed (workflow patch*) -> SRE; a 'patch: failed (<mode>)' issue has the log tail.
BASE=$(cd "$(dirname "$0")/.." && pwd)
source "$BASE/scripts/ci-lib.sh"
export LC_ALL=C.UTF-8
MODE=${1:?usage: ci-patch.sh scan|bundle|monthly}
LOCK_WORKFLOW="patch-$MODE"
trap unlock_hosts EXIT
bao_login ci-apply
export GITEA_API_TOKEN; GITEA_API_TOKEN=$(secret plan gitea_ci_token)
APPLY_TOKEN=$(secret apply pve_token_apply)
bao_logout

STAMP=$(date +%Y%m%d_%H%M)
MONTH=$(TZ=Asia/Kuala_Lumpur date +%Y-%m)
PREV=$(TZ=Asia/Kuala_Lumpur date -d "$(TZ=Asia/Kuala_Lumpur date +%Y-%m-15) -1 month" +%Y-%m)
KEEP=/var/lib/devops/patch; mkdir -p "$KEEP"
find "$KEEP" -mindepth 1 -maxdepth 1 -type d -mtime +35 -exec rm -rf {} + 2>/dev/null || true
export PATCH_OUT="$KEEP/$STAMP-$MODE"; mkdir -p "$PATCH_OUT"
W=$(mktemp -d); STD="$W/standards"
clone homelab/homelab-standards "$STD" "$GITEA_API_TOKEN"
RC=0; SNAP=""
# Patch health from the homelab-apps catalogue (proposal 3.2, migration step 9): one read-only clone of apps main per
# run (infra gitea_ci_token: read on apps). patch_run() passes apps_health.py's extra vars (patch_apps_health) to
# patch.yml; patch_apply runs those checks after each host. Clone/parse failed -> no file, a warning, and patch_apply
# keeps its built-in checks (fallback). Never blocks patching.
APPS_DIR="$W/apps"
if ! clone homelab/homelab-apps "$APPS_DIR" "$GITEA_API_TOKEN" > "$PATCH_OUT/apps-clone.log" 2>&1; then
  APPS_DIR=""; say "WARNING: homelab-apps clone failed: patch health falls back to the built-in checks"
fi
apps_health_args() {  # hosts -> sets APPS_HEALTH (extra-vars args) or leaves it empty (fallback)
  APPS_HEALTH=()
  if [ -z "$APPS_DIR" ]; then
    echo "homelab-apps clone failed: built-in checks only (fallback)" > "$PATCH_OUT/apps-health.txt"
  elif covered=$(python3 "$BASE/scripts/apps_health.py" build "$APPS_DIR" "$1" "$PATCH_OUT/apps-health.json" 2> "$PATCH_OUT/apps-health.err"); then
    APPS_HEALTH=(-e "@$PATCH_OUT/apps-health.json")
    echo "catalogue health for: ${covered:-no host (none in a deployment)}" > "$PATCH_OUT/apps-health.txt"
  else
    { echo "homelab-apps catalogue unreadable: built-in checks only (fallback)"; cat "$PATCH_OUT/apps-health.err"; } > "$PATCH_OUT/apps-health.txt"
  fi
  [ ${#APPS_HEALTH[@]} -gt 0 ] || say "WARNING: $(head -1 "$PATCH_OUT/apps-health.txt")"
  say "patch health: $(head -1 "$PATCH_OUT/apps-health.txt")"
}
PLAN() { INFRA_ROOT="$BASE" python3 "$BASE/scripts/patch_plan.py" "$@"; }
GITEA() { python3 "$BASE/scripts/gitea.py" "$@"; }

fail_issue() {  # what logfile
  { echo "**Patch run failed** (\`$MODE\`, $STAMP): $1"; echo
    echo "Results on the runner: \`$PATCH_OUT\`."
    [ -n "$SNAP" ] && echo "Pre-patch snapshot \`$SNAP\` (kept 7 days). Restoring is the SRE's call: \`snapshot.py rollback --name $SNAP --hosts <host>\`."
    echo; echo '```'; tail -60 "$2" 2>/dev/null || true; echo '```'; } > "$W/fail.md"
  GITEA issue-upsert "patch: failed ($MODE)" patch "$W/fail.md" || true
  RC=1
}

scan() {  # read-only; writes $PATCH_OUT/<host>.scan.json
  local ok=0 n
  (cd "$BASE/ansible" && INFRA_ROOT="$BASE" ansible-playbook -i ../scripts/inventory.py playbooks/patch-scan.yml) > "$PATCH_OUT/scan.log" 2>&1 || ok=$?
  ln -sfn "$PATCH_OUT" "$KEEP/latest"
  n=$(find "$PATCH_OUT" -maxdepth 1 -name '*.scan.json' | wc -l)
  say "scan: $n host(s) classified (ansible rc $ok)"
  grep -A40 'PLAY RECAP' "$PATCH_OUT/scan.log" || tail -30 "$PATCH_OUT/scan.log" || true
  [ "$ok" -eq 0 ] || fail_issue "scan failed on at least one host (the others are still handled)" "$PATCH_OUT/scan.log"
}

patch_run() {  # max_sev hosts [extra ansible args]: locks, then snapshot; only hosts with a snapshot are patched
  local sev=$1 hosts=$2 snapped ok=0; shift 2
  SNAP="pre-patch_$STAMP"
  if ! lock_hosts "$hosts"; then
    printf '%s\n' "${LOCK_NOTES[@]}" > "$PATCH_OUT/locks.txt"
    fail_issue "host locks not acquired for $hosts: nothing patched, no snapshot taken (issue 'pipeline: lock wait timeout <host>' names the holder)" "$PATCH_OUT/locks.txt"
    return 0
  fi
  [ ${#LOCK_NOTES[@]} -eq 0 ] || printf 'lock: %s\n' "${LOCK_NOTES[@]}"
  PROXMOX_VE_API_TOKEN="$APPLY_TOKEN" INFRA_ROOT="$BASE" python3 "$BASE/scripts/snapshot.py" take --name "$SNAP" --hosts "$hosts" > "$PATCH_OUT/snapshot.txt" 2>&1 || true
  cat "$PATCH_OUT/snapshot.txt"
  snapped=$(awk '$2=="ok"{print $1}' "$PATCH_OUT/snapshot.txt" | sort | paste -sd, -)
  if [ "$snapped" != "$hosts" ]; then
    fail_issue "no snapshot \`$SNAP\` for some of $hosts: those hosts were NOT patched" "$PATCH_OUT/snapshot.txt"
  fi
  if [ -n "$snapped" ]; then
    apps_health_args "$snapped"
    say "patch.yml max_sev=$sev snapshot=$SNAP on $snapped"
    (cd "$BASE/ansible" && INFRA_ROOT="$BASE" ansible-playbook -i ../scripts/inventory.py playbooks/patch.yml --limit "$snapped" \
      -e patch_max_sev="$sev" -e patch_snapshot="$SNAP" "${APPS_HEALTH[@]}" "$@") > "$PATCH_OUT/patch.log" 2>&1 || ok=$?
    grep -A40 'PLAY RECAP' "$PATCH_OUT/patch.log" || tail -40 "$PATCH_OUT/patch.log" || true
    [ "$ok" -eq 0 ] || fail_issue "patch.yml failed (max_sev=$sev); rollout stopped at the first bad host" "$PATCH_OUT/patch.log"
  fi
  unlock_hosts
}

case "$MODE" in
scan)
  scan
  HOSTS=$(PLAN sev12 "$PATCH_OUT" "$STD")
  if [ -n "$HOSTS" ]; then
    say "Sev1/2 on ring 1: $HOSTS"
    patch_run 2 "$HOSTS"
    PLAN summary "$PATCH_OUT" "Sev1/2 auto-apply $STAMP ($([ $RC -eq 0 ] && echo ok || echo FAILED))" "$PATCH_OUT/summary.md"
    GITEA issue-upsert "patch: Sev1/2 applied $MONTH" patch "$PATCH_OUT/summary.md" || true
  else
    say "no Sev1/2 packages on ring 1"
  fi
  PLAN reboot "$PATCH_OUT" || RC=1
  ;;
bundle)
  scan
  mkdir -p "$W/b"
  N=$(PLAN bundle "$PATCH_OUT" "$STD" "$MONTH" "$W/b")
  BR="patch/bundle-$MONTH"
  if [ "$N" = "0" ]; then
    say "no Sev3/4 packages on ring 1 for $MONTH: no bundle PR"
  elif [ -n "$(GITEA pr-find "$BR")" ]; then
    say "a PR from $BR already exists: not touched (a push would make Faris's approval stale)"
  else
    git -C "$BASE" worktree add -q -B "$BR" "$W/wt" HEAD
    mkdir -p "$W/wt/patches/$SITE"; cp "$W/b/$MONTH.json" "$W/b/$MONTH.md" "$W/wt/patches/$SITE/"
    git -C "$W/wt" add "patches/$SITE/$MONTH.json" "patches/$SITE/$MONTH.md"
    git -C "$W/wt" -c user.name=ci -c user.email=ci@hm1.invalid commit -q -m "patch($SITE): monthly bundle $MONTH"
    git -C "$W/wt" push -q "$(_proto)://ci:${GITEA_API_TOKEN}@$(_host)/${REPO}.git" "$BR"
    PRN=$(GITEA pr-open "$BR" "patch($SITE): monthly bundle $MONTH" "$W/b/$MONTH.md")
    say "bundle PR #$PRN opened for $N host(s)"
  fi
  PLAN reboot "$PATCH_OUT" || RC=1
  ;;
monthly)
  if ! python3 -I -c 'import calendar, datetime, sys, zoneinfo
t = datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Kuala_Lumpur"))
sys.exit(0 if t.weekday() == 5 and t.day + 7 > calendar.monthrange(t.year, t.month)[1] else 1)'; then
    echo "not the last Saturday of the month (MYT): nothing to do"; exit 0
  fi
  # Current month first; the previous month covers a last Saturday that fell before the 25th (bundle came later).
  for M in "$MONTH" "$PREV"; do
    if [ -f "$KEEP/monthly-$M.done" ]; then echo "bundle $M already applied"; continue; fi
    F="$BASE/patches/$SITE/$M.json"
    if [ ! -f "$F" ]; then echo "no bundle $M on main"; continue; fi
    if ! PRN=$(GITEA pr-gate "patch/bundle-$M" faris); then echo "bundle $M: not merged with Faris's approval, not applied"; continue; fi
    # Fail closed: without the allowlist in patch_apply, max_sev=4 would apply Sev3/4 packages Faris never saw.
    if ! grep -q patch_bundle_allow "$BASE/ansible/roles/patch_apply/tasks/main.yml"; then
      echo "patch_apply does not honour patch_bundle_allow" > "$PATCH_OUT/guard.txt"
      fail_issue "approved bundle $M NOT applied: role patch_apply lacks patch_bundle_allow (Configuration Engineer)" "$PATCH_OUT/guard.txt"
      break
    fi
    HOSTS=$(PLAN allow "$F" "$STD" "$PATCH_OUT/allow.json")
    if [ -z "$HOSTS" ]; then echo "bundle $M lists no ring-1 host"; touch "$KEEP/monthly-$M.done"; break; fi
    patch_run 4 "$HOSTS" -e "@$PATCH_OUT/allow.json"
    PLAN reboot "$PATCH_OUT" || RC=1
    PLAN summary "$PATCH_OUT" "Monthly bundle $M applied ($([ $RC -eq 0 ] && echo ok || echo FAILED))" "$PATCH_OUT/summary.md"
    GITEA comment "$PRN" "$PATCH_OUT/summary.md" || true
    [ $RC -eq 0 ] && touch "$KEEP/monthly-$M.done"
    break
  done
  ;;
*) echo "unknown mode $MODE"; exit 2 ;;
esac
exit $RC
