#!/usr/bin/env bash
# On push to main: apply the saved PR plans for the merged PR, then configure changed hosts with Ansible.
BASE=$(cd "$(dirname "$0")/.." && pwd)
source "$BASE/scripts/ci-lib.sh"
bao_login ci-apply
export GITEA_API_TOKEN; GITEA_API_TOKEN=$(secret plan gitea_ci_token)
APPLY_TOKEN=$(secret apply pve_token_apply); PG_CONN=$(secret plan pg_conn)
PLAN_PVE_TOKEN=$(secret plan pve_token_plan)   # read-only, for the L1 tests (decision 32)
# homelab-apps dispatch (proposal 2.1, migration step 9): OFF until cutover step 10(d) sets APPS_DISPATCH=1 in apply.yml.
# Off = the dispatch block below is skipped and this token is not even read. Missing key = warning, never a failure.
APPS_DISPATCH=${APPS_DISPATCH:-0}; APPS_DISPATCH_TOKEN=""
if [ "$APPS_DISPATCH" = "1" ]; then APPS_DISPATCH_TOKEN=$(secret apply gitea_apps_dispatch_token 2>/dev/null) || APPS_DISPATCH_TOKEN=""; fi
bao_logout

read -r PR HEAD_SHA <<<"$(python3 "$BASE/scripts/gitea.py" pr-of-commit "$SHA")" || true
if [ -z "${PR:-}" ]; then echo "No PR for $SHA; nothing to apply."; exit 0; fi
OUT="$PLANS/$HEAD_SHA"; W=$(mktemp -d); REPORT="$W/apply.md"; : > "$REPORT"
note() { echo "$*" >> "$REPORT"; }
LOCK_WORKFLOW=apply
trap unlock_hosts EXIT
finish() { unlock_hosts   # host locks released before the report (step 4b); process exit releases them too
  python3 "$BASE/scripts/gitea.py" comment "$PR" "$REPORT"
  python3 "$BASE/scripts/gitea.py" refresh-open "$PR" || true   # re-plan open PRs against the new state (#26)
  exit "$1"; }
export TF_VAR_standards_dir="$OUT/standards"
[ -d "$TF_VAR_standards_dir" ] || { note "## ❌ Apply: no saved plan for \`$HEAD_SHA\`"; finish 1; }

(cd "$BASE" && python3 scripts/changed.py "$SHA^1" "$SHA") > "$W/applied-changed.json"
mapfile -t STACKS < <(python3 -c 'import json,sys;print("\n".join(json.load(open(sys.argv[1]))["stacks"]))' "$W/applied-changed.json" | sed '/^$/d')
note "## Apply (\`${SHA:0:8}\`)"
# Templates must be clone-only for this token before anything is applied (decision 32 D5).
if ! PROXMOX_VE_API_TOKEN="$APPLY_TOKEN" python3 "$BASE/scripts/template-guard.py" "$TF_VAR_standards_dir" > "$W/template-guard.txt" 2>&1; then
  note "- ❌ **Template guard** — nothing applied"; note '```'; cat "$W/template-guard.txt" >> "$REPORT"; note '```'; finish 1
fi
note "- 🔒 $(tail -1 "$W/template-guard.txt")"

# ---- Which guests this apply touches (decision 32): plan actions + hosts Ansible will configure.
python3 "$BASE/scripts/plan_guests.py" "$OUT" "${STACKS[@]}" > "$W/plan-guests.json"
HOSTS=$(INFRA_ROOT="$BASE" python3 - "$W/applied-changed.json" <<'PY'
import json, os, sys
sys.path.insert(0, os.path.join(os.environ["INFRA_ROOT"], "scripts"))
from specs import load_specs
c = json.load(open(sys.argv[1])); have = {s["hostname"] for s in load_specs()}
print(",".join(sorted(have if c["ansible_all"] else set(c["hosts"]) & have)))
PY
)
# ---- Host locks (proposal 2.3, step 4b), BEFORE the pre-apply snapshot, held until finish():
# create ∪ replace ∪ update ∪ delete ∪ HOSTS; the whole fleet when ansible_all or tests/ changed. Hosts being created
# or destroyed may have no lock file yet / any more (the janitor creates files for specs on main): skipped, not waited.
read -r LOCK_SET LOCK_OPT <<<"$(INFRA_ROOT="$BASE" python3 - "$W/plan-guests.json" "$W/applied-changed.json" "$HOSTS" <<'PY'
import json, os, sys
sys.path.insert(0, os.path.join(os.environ["INFRA_ROOT"], "scripts"))
from specs import load_specs
g, c = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
have = {s["hostname"] for s in load_specs()}
deleted = {d["host"] for d in g["delete"]}
s = set(g["create"]) | set(g["replace"]) | set(g["update"]) | deleted | set(filter(None, sys.argv[3].split(",")))
if c["ansible_all"] or any(f.startswith("tests/") for f in c["files"]):
    s |= have
opt = set(g["create"]) | deleted
print((",".join(sorted(s)) or "-") + " " + (",".join(sorted(opt)) or "-"))
PY
)"
[ "$LOCK_SET" = "-" ] && LOCK_SET=""; [ "$LOCK_OPT" = "-" ] && LOCK_OPT=""
if [ -n "$LOCK_SET" ]; then
  if lock_hosts "$LOCK_SET" "$LOCK_OPT"; then
    note "- 🔐 Host locks: $(locked_hosts)"
    for n in "${LOCK_NOTES[@]}"; do note "  - ⚠️ $n"; done
  else
    note "- ❌ **Host locks** not acquired — nothing applied (issue \`pipeline: lock wait timeout <host>\` names the holder)"
    for n in "${LOCK_NOTES[@]}"; do note "  - $n"; done
    finish 1
  fi
fi
# Snapshot = existing guests that are updated or configured (not created/replaced/destroyed by this apply).
SNAP_HOSTS=$(python3 - "$W/plan-guests.json" "$HOSTS" <<'PY'
import json, sys
g = json.load(open(sys.argv[1])); new = set(g["create"]) | set(g["replace"]) | {d["host"] for d in g["delete"]}
print(",".join(sorted((set(g["update"]) | set(filter(None, sys.argv[2].split(",")))) - new)))
PY
)
SNAP="pre-apply_$(date +%Y%m%d_%H%M)"; SNAPPED=""
if [ -n "$SNAP_HOSTS" ]; then
  if PROXMOX_VE_API_TOKEN="$APPLY_TOKEN" INFRA_ROOT="$BASE" python3 "$BASE/scripts/snapshot.py" take --name "$SNAP" --hosts "$SNAP_HOSTS" > "$W/snapshot.txt" 2>&1; then
    note "- 📸 Snapshot \`$SNAP\` (kept 7 days): $SNAP_HOSTS"
  else
    note "- ⚠️ Snapshot \`$SNAP\` incomplete; no rollback for the hosts that failed:"; note '```'; cat "$W/snapshot.txt" >> "$REPORT"; note '```'
  fi
  SNAPPED=$(awk '$2=="ok"{print $1}' "$W/snapshot.txt" | paste -sd, -)
fi
FAILED=0; ANSIBLE_RC=0; RB=""   # ANSIBLE_RC / RB (stg rollbacks) feed the apps dispatch at the end
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

# ---- Forget stale SSH host keys of guests CREATED or REPLACED by this apply (issue #6).
# A new guest that reuses an IP has a new host key; with StrictHostKeyChecking=accept-new (ansible.cfg) ssh refuses
# the mismatch instead of recording it. Only IPs of create/replace actions in the applied plans are touched.
NEW_IPS=()
if [ ${#STACKS[@]} -gt 0 ]; then
  if INFRA_ROOT="$BASE" python3 - "$OUT" "${STACKS[@]}" > "$W/new-ips.txt" 2> "$W/new-ips.err" <<'PY'
import ipaddress, json, os, sys
sys.path.insert(0, os.path.join(os.environ["INFRA_ROOT"], "scripts"))
from specs import load_specs
out, stacks = sys.argv[1], sys.argv[2:]
ip_of = {s.get("hostname"): str(s.get("ip") or "") for s in load_specs()}
ips = set()
for st in stacks:
    parts = st.rstrip("/").split("/")
    plan = os.path.join(out, f"{parts[-2]}-{parts[-1]}.plan.json")
    if not os.path.exists(plan):
        print(f"no plan json for {st}", file=sys.stderr)
        continue
    for rc in json.load(open(plan)).get("resource_changes", []):
        if rc.get("type") not in ("proxmox_virtual_environment_container", "proxmox_virtual_environment_vm"):
            continue
        if "create" not in (rc.get("change") or {}).get("actions", []):  # create, or replace (delete+create)
            continue
        ip = ip_of.get(rc.get("index"), "")
        if not ip:
            try:
                ip = rc["change"]["after"]["initialization"][0]["ip_config"][0]["ipv4"][0]["address"] or ""
            except (KeyError, IndexError, TypeError):
                ip = ""
        ip = ip.split("/")[0].strip()
        try:
            ips.add(str(ipaddress.ip_address(ip)))
        except ValueError:
            if ip:
                print(f"skipping invalid ip {ip!r}", file=sys.stderr)
print("\n".join(sorted(ips)))
PY
  then
    mapfile -t NEW_IPS < <(sed '/^$/d' "$W/new-ips.txt")
  else
    note "- ⚠️ Could not determine created/replaced guest IPs; known_hosts left unchanged: $(tail -1 "$W/new-ips.err" 2>/dev/null || true)"
  fi
fi
if [ ${#NEW_IPS[@]} -gt 0 ]; then
  KH_HOME="${HOME:-$(getent passwd "$(id -u)" | cut -d: -f6)}"
  for ip in "${NEW_IPS[@]}"; do
    # The known_hosts files ssh (and therefore Ansible) actually uses for this host as the runner user.
    mapfile -t KH < <(ssh -G -l root "$ip" 2>/dev/null | awk '$1=="userknownhostsfile"{for(i=2;i<=NF;i++)print $i}' | sed "s|^~|$KH_HOME|" || true)
    [ ${#KH[@]} -gt 0 ] || KH=("$KH_HOME/.ssh/known_hosts")
    for f in "${KH[@]}"; do
      if [ -f "$f" ]; then ssh-keygen -R "$ip" -f "$f" >/dev/null 2>&1 || true; fi
    done
  done
  note "- 🔑 Cleared old SSH host keys for created/replaced guest(s): ${NEW_IPS[*]}"
fi

# ---- Ansible: changed hosts that exist, or every host when ansible/ changed (HOSTS, computed above)
if [ -n "$HOSTS" ]; then
  # ci-apps key (proposal 5.1, infra 3.3-1): base_common installs ansible/keys/ci-apps.pub only on hosts listed in a
  # homelab-apps deployment (extra var base_ci_apps_hosts) and removes it elsewhere. If the apps clone or its parse
  # fails, base_ci_apps_hosts stays undefined: existing ci-apps keys are left unchanged (never removed), the apply
  # warns and upserts issue "pipeline: apps clone failed".
  APPS_ARGS=()
  if clone homelab/homelab-apps "$W/apps" "$GITEA_API_TOKEN" >/dev/null 2>&1 \
     && python3 "$BASE/scripts/apps_hosts.py" "$W/apps" > "$W/apps-hosts.json" 2> "$W/apps-hosts.err"; then
    APPS_ARGS=(-e "@$W/apps-hosts.json")
    note "- 🗝️ ci-apps key hosts (homelab-apps deployments): $(python3 -c 'import json,sys;print(", ".join(json.load(open(sys.argv[1]))["base_ci_apps_hosts"]) or "none")' "$W/apps-hosts.json")"
  else
    note "- ⚠️ homelab-apps clone/parse failed: existing ci-apps keys left unchanged"
    { echo "**homelab-apps clone failed** during the apply of PR #$PR (\`${SHA:0:8}\`): the \`ci-apps\` key was neither"
      echo "installed nor removed on any host (existing keys left unchanged)."; echo
      echo '```'; tail -5 "$W/apps-hosts.err" 2>/dev/null | tr -d '`' || true; echo '```'; } > "$W/apps-clone.md"
    python3 "$BASE/scripts/gitea.py" issue-upsert "pipeline: apps clone failed" pipeline "$W/apps-clone.md" || true
  fi
  # v7: short-lived ci-apply token so base_common can write each new guest's admin password ONCE (create-only) to
  # OpenBao <zone>/secret/guests/<host>/<instance>; revoked right after the run.
  bao_login ci-apply; export GUEST_SECRET_TOKEN="$BAO_TOKEN" BAO_ZONE_MODE="${BAO_ZONE_MODE:-namespace}"
  ok=0; (cd "$BASE/ansible" && INFRA_ROOT="$BASE" ansible-playbook -i ../scripts/inventory.py playbooks/site.yml --limit "$HOSTS" "${APPS_ARGS[@]}") > "$W/ansible.log" 2>&1 || ok=$?
  BAO_TOKEN="$GUEST_SECRET_TOKEN" bao_logout; unset GUEST_SECRET_TOKEN
  ANSIBLE_RC=$ok
  if [ $ok -eq 0 ]; then
    note "- ✅ Ansible: $HOSTS — $(grep -A20 'PLAY RECAP' "$W/ansible.log" | grep -c 'failed=0') host(s) ok"
  else
    note "- ❌ **Ansible** on $HOSTS"; note '```'; tail -40 "$W/ansible.log" >> "$REPORT"; note '```'; FAILED=1
  fi
else
  note "- No hosts to configure."
fi
# ---- Tests (decision 32): L1-L3 for created/changed/configured guests, "gone" for destroyed ones.
# tests/ changed -> every guest is re-verified.
read -r VHOSTS GONE <<<"$(INFRA_ROOT="$BASE" python3 - "$W/plan-guests.json" "$W/applied-changed.json" "$HOSTS" <<'PY'
import json, os, sys
sys.path.insert(0, os.path.join(os.environ["INFRA_ROOT"], "scripts"))
from specs import load_specs
g, c = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
have = {s["hostname"] for s in load_specs()}
v = set(g["create"]) | set(g["replace"]) | set(g["update"]) | set(filter(None, sys.argv[3].split(",")))
if any(f.startswith("tests/") for f in c["files"]):
    v = have
gone = [f"{d['host']}:{d['vmid']}:{d['ip']}" for d in g["delete"] if d["host"] not in have]
print((",".join(sorted(v & have)) or "-") + " " + (",".join(gone) or "-"))
PY
)"
[ "$VHOSTS" = "-" ] && VHOSTS=""; [ "$GONE" = "-" ] && GONE=""
if [ -n "$VHOSTS$GONE" ]; then
  vok=0
  PROXMOX_VE_API_TOKEN="$PLAN_PVE_TOKEN" INFRA_ROOT="$BASE" python3 "$BASE/scripts/verify.py" --standards "$TF_VAR_standards_dir" \
    --hosts "$VHOSTS" --gone "$GONE" --out "$W/verify" > "$W/verify.log" 2>&1 || vok=$?
  cp "$W/verify/verify.json" "$OUT/verify.json" 2>/dev/null || true
  if [ -f "$W/verify/verify.md" ]; then note ""; note "### Tests"; cat "$W/verify/verify.md" >> "$REPORT"
  else note "- ❌ **Tests** did not run"; note '```'; tail -20 "$W/verify.log" >> "$REPORT"; note '```'; vok=1; fi
  if [ $vok -ne 0 ]; then
    FAILED=1
    # D3: auto-rollback only in stg, only failed guests that have this apply's snapshot; new guests are never destroyed.
    RB=$(INFRA_ROOT="$BASE" python3 - "$W/verify/verify.json" "$SNAPPED" <<'PY'
import json, os, sys
sys.path.insert(0, os.path.join(os.environ["INFRA_ROOT"], "scripts"))
from specs import load_specs
zone = {s["hostname"]: s["_zone"] for s in load_specs()}
try:
    failed = {r["host"] for r in json.load(open(sys.argv[1]))["rows"] if r["status"] == "fail"}
except Exception:
    failed = set()
print(",".join(sorted(h for h in failed & set(filter(None, sys.argv[2].split(","))) if zone.get(h) == "stg")))
PY
)
    if [ -n "$RB" ]; then
      PROXMOX_VE_API_TOKEN="$APPLY_TOKEN" INFRA_ROOT="$BASE" python3 "$BASE/scripts/snapshot.py" rollback --name "$SNAP" --hosts "$RB" > "$W/rollback.txt" 2>&1 || true
      note ""; note "### ↩️ Auto-rollback (stg) to \`$SNAP\`"; note '```'; cat "$W/rollback.txt" >> "$REPORT"; note '```'
      rok=0; PROXMOX_VE_API_TOKEN="$PLAN_PVE_TOKEN" INFRA_ROOT="$BASE" python3 "$BASE/scripts/verify.py" --standards "$TF_VAR_standards_dir" \
        --hosts "$RB" --out "$W/verify-rb" > "$W/verify-rb.log" 2>&1 || rok=$?
      note "Re-test after rollback: $([ $rok -eq 0 ] && echo '✅ green' || echo '❌ still failing')"
      [ -f "$W/verify-rb/verify.md" ] && cat "$W/verify-rb/verify.md" >> "$REPORT"
      note "The merged change is still on main: fix it forward or revert it by PR."
    fi
  fi
fi
# ---- homelab-apps dispatch (proposal 2.1, migration step 9). After the tests (incl. stg rollback re-test), before
# finish(): the host locks are still held, so the apps deploy waits for them. Hosts = VHOSTS with verify rows and no
# failed row, minus stg rollbacks, that are in a homelab-apps deployment (apps_dispatch.py). Ansible failed = nothing.
# A dispatch problem NEVER fails the apply: a warning line + issue "pipeline: apps dispatch failed" (PM -> oc-infra).
# APPS_DISPATCH != 1 (default until step 10(d)): nothing here runs and the report is unchanged.
apps_dispatch_failed() {  # reason [hosts]
  note "- ⚠️ apps dispatch failed: $1 (the apply result is unaffected)"
  { echo "**homelab-apps dispatch failed** after the apply of PR #$PR (\`${SHA:0:8}\`): $1."; echo
    echo "Hosts that would have been dispatched: \`${2:-not computed}\`. Once fixed, dispatch homelab-apps \`deploy\` for them."
    echo; echo '```'; tail -5 "$W/apps-dispatch.err" 2>/dev/null | tr -d '`' || true; echo '```'; } > "$W/apps-dispatch.md"
  python3 "$BASE/scripts/gitea.py" issue-upsert "pipeline: apps dispatch failed" pipeline "$W/apps-dispatch.md" || true
}
if [ "$APPS_DISPATCH" = "1" ] && [ -n "$VHOSTS" ]; then
  : > "$W/apps-dispatch.err"
  if [ "$ANSIBLE_RC" -ne 0 ]; then
    note "- ⏭️ apps dispatch: none (Ansible failed)"
  elif [ -z "$APPS_DISPATCH_TOKEN" ]; then
    apps_dispatch_failed "no gitea_apps_dispatch_token in hm1/platform/ci/apply"
  elif ! { [ -d "$W/apps/.git" ] || clone homelab/homelab-apps "$W/apps" "$GITEA_API_TOKEN" >/dev/null 2>"$W/apps-dispatch.err"; }; then
    apps_dispatch_failed "homelab-apps clone failed"
  elif ! DHOSTS=$(INFRA_ROOT="$BASE" python3 "$BASE/scripts/apps_dispatch.py" hosts "$W/apps" "$W/verify/verify.json" "$VHOSTS" "$RB" 2>"$W/apps-dispatch.err"); then
    apps_dispatch_failed "could not read the homelab-apps deployments"
  elif [ -z "$DHOSTS" ]; then
    note "- apps dispatch: none (no green host of this apply is in a homelab-apps deployment)"
  elif APPS_DISPATCH_TOKEN="$APPS_DISPATCH_TOKEN" python3 "$BASE/scripts/apps_dispatch.py" send "$DHOSTS" "$PR" >/dev/null 2>"$W/apps-dispatch.err"; then
    note "- 🚀 homelab-apps \`deploy\` dispatched (ref main) for: $DHOSTS (it waits for these host locks)"
  else
    apps_dispatch_failed "dispatch API call failed" "$DHOSTS"
  fi
fi
unset APPS_DISPATCH_TOKEN
[ $FAILED -eq 0 ] && finish 0 || finish 1
