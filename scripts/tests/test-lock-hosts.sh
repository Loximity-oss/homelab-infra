#!/usr/bin/env bash
# Unit test for lock_hosts / unlock_hosts (scripts/ci-lib.sh, migration step 4b; proposal 2.3, SRE 3.7-2).
# Self-contained: temp lock dir, no Gitea (the issue upsert is captured), no OpenBao, no root. Run on CT 300 or
# anywhere with bash >= 4.3, flock and GNU dd:   bash scripts/tests/test-lock-hosts.sh   (exit 0 = all pass)
# Main case: an APPLY waits on a lock held by another run (a patch run), times out, takes no lock, creates no file,
# and the 'pipeline: lock wait timeout <host>' issue names the holder from its .owner sidecar.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'kill $(jobs -p) 2>/dev/null; rm -rf "$T"' EXIT
export LOCK_DIR="$T/locks" LOCK_WAIT=2 LOCK_FILE_WAIT=2 LOCK_POLL=1
mkdir -p "$LOCK_DIR" "$T/issues"
source "$HERE/../ci-lib.sh"
set +e
_lock_issue_upsert() { cp "$3" "$T/issues/$(echo "$1" | tr ' :/' '___')"; echo "$1" >> "$T/issues/titles"; }
PASS=0; FAILN=0
ok() { echo "PASS $*"; PASS=$((PASS + 1)); }
bad() { echo "FAIL $*"; FAILN=$((FAILN + 1)); }
check() { local what=$1; shift; if "$@"; then ok "$what"; else bad "$what"; fi; }
mk() { for h in "$@"; do : > "$LOCK_DIR/host-$h.lock"; : > "$LOCK_DIR/host-$h.owner"; done; }   # = the janitor
listing() { (cd "$LOCK_DIR" && ls -A | sort); }
A=hm1-stg-test01; B=hm1-stg-test02; C=hm1-stg-test03
mk "$A" "$B" "$C"

# --- 1. a free set is locked, owner written in place, released and blanked
LOCK_WORKFLOW=apply REPO=homelab/homelab-infra GITHUB_RUN_ID=111
before=$(listing)
check "free hosts are locked" lock_hosts "$C,$A"
check "locked set is sorted" test "$(locked_hosts)" = "$A,$C"
check "owner names the run" grep -qx 'workflow=apply' "$LOCK_DIR/host-$A.owner"
check "lock really held" bash -c "! flock -n '$LOCK_DIR/host-$A.lock' true"
unlock_hosts
check "released after unlock" flock -n "$LOCK_DIR/host-$A.lock" true
check "owner blanked on release" test ! -s "$LOCK_DIR/host-$A.owner"
check "no file created or removed" test "$(listing)" = "$before"

# --- 2. MAIN CASE: an apply waits on a held lock; the timeout issue names the owner
printf 'run=4242\nurl=http://gitea/homelab/homelab-infra/actions/runs/77\nrepo=homelab/homelab-infra\nworkflow=patch-scan\npid=31337\nhost=hm1-shr-cicd01\nstart=2026-10-20T03:40:00Z\n' > "$LOCK_DIR/host-$B.owner"
flock "$LOCK_DIR/host-$B.lock" sleep 30 & HOLDER=$!
sleep 0.5
rm -f "$T/issues/titles"
t0=$(date +%s)
lock_hosts "$A,$B,$C"; rc=$?
t1=$(date +%s)
check "apply lock call fails on timeout (rc=1)" test "$rc" -eq 1
check "it waited about LOCK_WAIT" test $((t1 - t0)) -ge 2
check "no lock left held after a failed call" test -z "$(locked_hosts)"
check "host locked before the timeout was released" flock -n "$LOCK_DIR/host-$A.lock" true
check "issue title names the host" grep -qx "pipeline: lock wait timeout $B" "$T/issues/titles"
I="$T/issues/pipeline_lock_wait_timeout_$B"
check "issue names the holder's workflow" grep -q 'workflow=patch-scan' "$I"
check "issue names the holder's run" grep -q 'run=4242' "$I"
check "issue names the holder's pid" grep -q 'pid=31337' "$I"
check "issue says the holder may be stale" grep -qi 'stale' "$I"
check "issue names the waiting apply" grep -q 'workflow=apply' "$I"
check "holder's owner file untouched" grep -q 'workflow=patch-scan' "$LOCK_DIR/host-$B.owner"
check "no file created or removed" test "$(listing)" = "$before"

# --- 3. the same apply succeeds when the holder releases within the wait
kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null
flock "$LOCK_DIR/host-$B.lock" sleep 1 & HOLDER=$!
sleep 0.3
LOCK_WAIT=10
check "waiting apply gets the lock after release" lock_hosts "$A,$B"
check "owner now names the apply" grep -qx 'workflow=apply' "$LOCK_DIR/host-$B.owner"
unlock_hosts; wait "$HOLDER" 2>/dev/null; LOCK_WAIT=2

# --- 4. never creates: missing lock or owner file
D=hm1-stg-test04
rm -f "$T/issues/titles"
lock_hosts "$D"; rc=$?
check "missing lock file fails after LOCK_FILE_WAIT" test "$rc" -eq 1
check "missing lock file was not created" test ! -e "$LOCK_DIR/host-$D.lock"
check "missing lock file raises the timeout issue" grep -qx "pipeline: lock wait timeout $D" "$T/issues/titles"
check "optional host without lock file is skipped" lock_hosts "$A,$D" "$D"
check "only the existing host is locked" test "$(locked_hosts)" = "$A"
unlock_hosts
: > "$LOCK_DIR/host-$D.lock"            # lock file without .owner
check "lock without owner file still locks" lock_hosts "$D"
check "owner file was not created" test ! -e "$LOCK_DIR/host-$D.owner"
unlock_hosts; rm -f "$LOCK_DIR/host-$D.lock"

# --- 5. refuses symlinks and bad names, never writes through a symlinked owner file
ln -s "$T/elsewhere" "$LOCK_DIR/host-hm1-stg-link01.lock"
lock_hosts "hm1-stg-link01"; rc=$?
check "symlinked lock file refused (rc=2)" test "$rc" -eq 2
rm -f "$LOCK_DIR/host-hm1-stg-link01.lock"
lock_hosts "../etc/passwd"; rc=$?
check "path-like hostname refused (rc=2)" test "$rc" -eq 2
: > "$T/target"; mv "$LOCK_DIR/host-$C.owner" "$T/c.owner"; ln -s "$T/target" "$LOCK_DIR/host-$C.owner"
lock_hosts "$C" >/dev/null 2>&1
check "symlinked owner file is not written through" test ! -s "$T/target"
unlock_hosts; rm -f "$LOCK_DIR/host-$C.owner"; mv "$T/c.owner" "$LOCK_DIR/host-$C.owner"

# --- 6. the lock is opened read-only: a read-only lock file still works
chmod 0444 "$LOCK_DIR/host-$A.lock"
check "read-only lock file can be locked" lock_hosts "$A"
unlock_hosts; chmod 0644 "$LOCK_DIR/host-$A.lock"

echo "== $PASS passed, $FAILN failed"
[ "$FAILN" -eq 0 ]
