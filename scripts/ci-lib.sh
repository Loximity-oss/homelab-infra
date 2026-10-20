# Shared CI helpers. Sourced by ci-pr.sh / ci-apply.sh / ci-drift.sh (always from the trusted main checkout).
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
PLANS=/var/lib/devops/plans
SITE=hm1
export BAO_ADDR=https://192.168.70.12:8200 BAO_CACERT=/etc/openbao/ca.crt
CI_LIB_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

_proto() { echo "${SERVER%%://*}"; }
_host() { echo "${SERVER#*://}" | sed 's|/$||'; }
clone() { git clone -q "$(_proto)://ci:$3@$(_host)/$1.git" "$2" && git -C "$2" remote set-url origin "$SERVER/$1.git"; }  # no token left on disk
bao_login() {                                                            # ci-plan | ci-apply (AppRole, files readable only by that runner's user)
  BAO_TOKEN=$(bao write -field=token auth/approle/login role_id=@/etc/openbao/$1/role_id secret_id=@/etc/openbao/$1/secret_id); export BAO_TOKEN; }
secret() { bao kv get -mount=secret -field="$2" "hm1/platform/ci/$1"; }   # plan|apply key
bao_logout() { bao token revoke -self >/dev/null 2>&1 || true; unset BAO_TOKEN; }
stack_name() { echo "$1" | awk -F/ '{print $(NF-1)"-"$NF}'; }            # terraform/stacks/hm1/shr -> hm1-shr
stack_schema() { echo "$1" | awk -F/ '{print $(NF-1)"_"$NF}'; }          # -> hm1_shr
tf_init() {                                                              # dir
  terraform -chdir="$1" init -input=false -no-color -reconfigure \
    -backend-config="conn_str=$PG_CONN" -backend-config="schema_name=$(stack_schema "$1")" >/dev/null
}
all_stacks() { (cd "$1" && ls -d terraform/stacks/*/*/ 2>/dev/null | sed 's|/$||' | while read -r d; do [ -f "$d/main.tf" ] && echo "$d"; done); }
say() { echo "::group::$*" 2>/dev/null || true; echo "== $*"; }
spec_hosts() { INFRA_ROOT="${1:-$(dirname "$CI_LIB_DIR")}" python3 -c 'import os,sys; sys.path.insert(0, os.path.join(os.environ["INFRA_ROOT"], "scripts")); from specs import load_specs; print(",".join(sorted(s["hostname"] for s in load_specs())))'; }

# ---- Host locks (proposal 2-orchestration 2.3, migration step 4b; also used by homelab-apps from its infra clone)
# /var/lib/devops/locks is root:devops-locks 3770 and CT 300 has fs.protected_regular=2. The root janitor
# (host/apps-runner/apps-janitor) creates host-<hostname>.lock and .owner (root, 0664) for every guest spec on main.
# lock_hosts NEVER creates, deletes or renames a file: each lock is opened read-only (exec {fd}<file; flock works on
# a read fd) and the .owner sidecar is rewritten in place (dd conv=nocreat, oflag=nofollow).
# Sorted, one fd per host, all hosts of a run in ONE call (no lock-order deadlock), flock -w LOCK_WAIT per host.
# A wait timeout upserts issue "pipeline: lock wait timeout <host>" (label pipeline) naming the holder from .owner.
# Locks are released by unlock_hosts (owner blanked while still held) or by process exit (flock).
LOCK_DIR=${LOCK_DIR:-/var/lib/devops/locks}
LOCK_WAIT=${LOCK_WAIT:-3600}              # s per host; job timeout-minutes 240 > 60 min wait + longest run
LOCK_FILE_WAIT=${LOCK_FILE_WAIT:-660}     # s to wait for a lock file the janitor has not created yet (timer: 10 min)
LOCK_POLL=${LOCK_POLL:-15}
declare -A LOCK_FDS=()
LOCK_NOTES=()                             # human-readable warnings/errors of the last lock_hosts call

lock_owner_text() {   # the .owner content of this run (KEY=VALUE lines)
  local url=""
  [ -n "${SERVER:-}" ] && [ -n "${REPO:-}" ] && [ -n "${GITHUB_RUN_NUMBER:-}" ] && url="${SERVER%/}/$REPO/actions/runs/$GITHUB_RUN_NUMBER"
  printf 'run=%s\nurl=%s\nrepo=%s\nworkflow=%s\npid=%s\nhost=%s\nstart=%s\n' \
    "${GITHUB_RUN_ID:-${GITHUB_RUN_NUMBER:-manual}}" "$url" "${REPO:-${GITHUB_REPOSITORY:-unknown}}" \
    "${LOCK_WORKFLOW:-${GITHUB_WORKFLOW:-unknown}}" "$$" "$(hostname 2>/dev/null || echo '?')" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
_lock_owner_write() { # file text: rewrite an EXISTING regular file in place; never creates, never follows a symlink
  [ -f "$1" ] && [ ! -L "$1" ] || return 1
  printf '%s' "$2" | dd of="$1" conv=nocreat oflag=nofollow status=none 2>/dev/null
}
_lock_owner_read() {  # host: the holder as recorded, printable characters only, no backticks
  local f="$LOCK_DIR/host-$1.owner"
  if [ -f "$f" ] && [ ! -L "$f" ] && [ -s "$f" ]; then head -c 2000 "$f" | tr -cd '[:print:]\n' | tr -d '`'
  else echo "(no owner recorded)"; fi
}
_lock_issue_upsert() { python3 "$CI_LIB_DIR/gitea.py" issue-upsert "$@"; }   # title label file (tests override this)
lock_timeout_issue() {  # host reason
  local h=$1 t
  t=$(mktemp)
  { echo "**Lock wait timeout** on \`$h\`: $2. This run did not touch the host."
    echo
    echo "**Holder** (from \`$LOCK_DIR/host-$h.owner\`):"
    echo '```'; _lock_owner_read "$h"; echo '```'
    echo "The holder may be **stale**: flock releases a lock when its process dies, so if that pid/run is gone the"
    echo "owner file is out of date and the lock is held by another (unrecorded) process, or by a child of the holder."
    echo "Nobody clears locks by hand (SRE runbook: stale locks)."
    echo
    echo "**Waiting run:**"
    echo '```'; lock_owner_text | tr -d '`'; echo '```'
  } > "$t"
  _lock_issue_upsert "pipeline: lock wait timeout $h" pipeline "$t" >/dev/null 2>&1 || LOCK_NOTES+=("could not upsert issue 'pipeline: lock wait timeout $h'")
  rm -f "$t"
}

lock_hosts() {  # csv [optional_csv]: optional hosts without a lock file yet are skipped with a note, not waited for
  local want=() h f fd waited opt=",${2:-},"
  LOCK_NOTES=()
  mapfile -t want < <(tr ',' '\n' <<<"$1" | sed '/^$/d' | sort -u)
  for h in "${want[@]}"; do
    if ! [[ $h =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]]; then LOCK_NOTES+=("bad hostname '$h'"); unlock_hosts; return 2; fi
  done
  for h in "${want[@]}"; do
    [ -n "${LOCK_FDS[$h]:-}" ] && continue
    f="$LOCK_DIR/host-$h.lock"; waited=0
    while [ -L "$f" ] || [ ! -f "$f" ]; do
      if [ -L "$f" ]; then LOCK_NOTES+=("$f is a symlink: refused"); unlock_hosts; return 2; fi
      if [[ $opt == *",$h,"* ]]; then LOCK_NOTES+=("no lock file for $h yet (janitor): not locked"); continue 2; fi
      if [ "$waited" -ge "$LOCK_FILE_WAIT" ]; then
        LOCK_NOTES+=("$f missing after ${waited}s")
        lock_timeout_issue "$h" "lock file \`$f\` still missing after ${waited} s (the root janitor creates it every 10 min; runners never create lock files)"
        unlock_hosts; return 1
      fi
      sleep "$LOCK_POLL"; waited=$((waited + LOCK_POLL))
    done
    if ! exec {fd}<"$f"; then LOCK_NOTES+=("cannot open $f read-only"); unlock_hosts; return 1; fi
    if ! flock -n "$fd"; then
      LOCK_NOTES+=("waited for $h (holder: $(_lock_owner_read "$h" | tr '\n' ' '))")
      echo "lock_hosts: waiting up to ${LOCK_WAIT}s for $h" >&2
      if ! flock -w "$LOCK_WAIT" "$fd"; then
        exec {fd}<&-
        LOCK_NOTES+=("timeout after ${LOCK_WAIT}s waiting for $h")
        lock_timeout_issue "$h" "not free within ${LOCK_WAIT} s"
        unlock_hosts; return 1
      fi
    fi
    LOCK_FDS[$h]=$fd
    _lock_owner_write "$LOCK_DIR/host-$h.owner" "$(lock_owner_text)" || LOCK_NOTES+=("owner file for $h not written (missing or not a regular file)")
  done
  return 0
}

unlock_hosts() {  # [csv]: release these (default: all held); the owner file is blanked while the lock is still held
  local h fd
  for h in "${!LOCK_FDS[@]}"; do
    if [ -n "${1:-}" ] && [[ ",$1," != *",$h,"* ]]; then continue; fi
    fd=${LOCK_FDS[$h]}
    _lock_owner_write "$LOCK_DIR/host-$h.owner" "" || true
    flock -u "$fd" 2>/dev/null || true
    exec {fd}<&-
    unset 'LOCK_FDS[$h]'
  done
  return 0
}
locked_hosts() { printf '%s\n' "${!LOCK_FDS[@]}" | sed '/^$/d' | sort | paste -sd, -; }
