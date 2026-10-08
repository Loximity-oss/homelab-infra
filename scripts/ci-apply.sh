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
# Templates must be clone-only for this token before anything is applied (decision 32 D5).
if ! PROXMOX_VE_API_TOKEN="$APPLY_TOKEN" python3 "$BASE/scripts/template-guard.py" "$TF_VAR_standards_dir" > "$W/template-guard.txt" 2>&1; then
  note "- ❌ **Template guard** — nothing applied"; note '```'; cat "$W/template-guard.txt" >> "$REPORT"; note '```'; finish 1
fi
note "- 🔒 $(tail -1 "$W/template-guard.txt")"
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
  # v7: short-lived ci-apply token so base_common can write each new guest's admin password ONCE (create-only) to
  # OpenBao <zone>/secret/guests/<host>/<instance>; revoked right after the run.
  bao_login ci-apply; export GUEST_SECRET_TOKEN="$BAO_TOKEN" BAO_ZONE_MODE="${BAO_ZONE_MODE:-namespace}"
  ok=0; (cd "$BASE/ansible" && INFRA_ROOT="$BASE" ansible-playbook -i ../scripts/inventory.py playbooks/site.yml --limit "$HOSTS") > "$W/ansible.log" 2>&1 || ok=$?
  BAO_TOKEN="$GUEST_SECRET_TOKEN" bao_logout; unset GUEST_SECRET_TOKEN
  if [ $ok -eq 0 ]; then
    note "- ✅ Ansible: $HOSTS — $(grep -A20 'PLAY RECAP' "$W/ansible.log" | grep -c 'failed=0') host(s) ok"
  else
    note "- ❌ **Ansible** on $HOSTS"; note '```'; tail -40 "$W/ansible.log" >> "$REPORT"; note '```'; finish 1
  fi
else
  note "- No hosts to configure."
fi
finish 0
