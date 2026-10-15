#!/usr/bin/env bash
# Template builds and refresh checks (homelab-standards PR #17, docs/templates.md). Runner host-template, Unix user
# runner-template, AppRole ci-template (reads only secret/hm1/platform/ci/template). Scripts come from main.
# MODE=build: push to main touching terraform/templates/** (or dispatch with SPEC). MODE=refresh-check: weekly, no PVE.
BASE=$(cd "$(dirname "$0")/.." && pwd)
source "$BASE/scripts/ci-lib.sh"
MODE=${MODE:-build}
if [ "${EVENT:-}" = workflow_dispatch ] && [ -z "${SPEC:-}" ]; then MODE=refresh-check; fi
LOCK=/var/lib/devops/locks/templates.lock   # apply.yml takes it shared: no guest clones a template mid-replace

bao_login ci-template
export GITEA_API_TOKEN; GITEA_API_TOKEN=$(secret template gitea_ci_token)
if [ "$MODE" = build ]; then PVE_TOKEN=$(secret template pve_token_template); export PVE_TOKEN; fi
bao_logout
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
clone homelab/homelab-standards "$W/standards" "$GITEA_API_TOKEN"

if [ "$MODE" = refresh-check ]; then
  mkdir -p "$W/refresh"
  python3 "$BASE/scripts/template-build.py" refresh-check "$BASE" "$W/standards" --out "$W/refresh"
  for f in "$W"/refresh/*.md; do
    [ -e "$f" ] || continue
    python3 "$BASE/scripts/gitea.py" issue-upsert "template refresh: $(basename "$f" .md)" template "$f"
  done
  exit 0
fi

REPORT="$W/report.md"
PR=""; read -r PR _ <<<"$(python3 "$BASE/scripts/gitea.py" pr-of-commit "$SHA")" || true
post() { if [ -n "$PR" ]; then python3 "$BASE/scripts/gitea.py" comment "$PR" "$REPORT"; else cat "$REPORT"; fi; }
if [ ! -d "$(dirname "$LOCK")" ]; then
  printf '## ❌ Template build\n\n- lock directory %s missing (root step on CT 300); nothing built\n' "$(dirname "$LOCK")" > "$REPORT"
  post; exit 1
fi
exec 9>>"$LOCK"
if ! flock -x -w 3600 9; then
  printf '## ❌ Template build\n\n- template lock not free within 1h (guest apply running?); nothing built\n' > "$REPORT"; post; exit 1
fi
if ! PROXMOX_VE_API_TOKEN="$PVE_TOKEN" python3 "$BASE/scripts/template-builder-guard.py" "$W/standards" > "$W/guard.txt" 2>&1; then
  { echo "## ❌ Template build: builder guard - nothing built"; echo; echo '```'; cat "$W/guard.txt"; echo '```'; } > "$REPORT"
  post; exit 1
fi
if [ -n "${SPEC:-}" ]; then ARGS=(--spec "$SPEC"); else ARGS=(--diff "$SHA^1" "$SHA"); fi
rc=0
SHA="$SHA" python3 "$BASE/scripts/template-build.py" build "$BASE" "$W/standards" --report "$W/build.md" "${ARGS[@]}" || rc=$?
{ cat "$W/build.md" 2>/dev/null || echo "## ❌ Template build: no report"; echo; echo "- 🔒 $(tail -1 "$W/guard.txt")"; } > "$REPORT"
post
exit $rc
