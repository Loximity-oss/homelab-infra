# Shared CI helpers. Sourced by ci-pr.sh / ci-apply.sh / ci-drift.sh (always from the trusted main checkout).
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
PLANS=/var/lib/devops/plans
SITE=hm1
export BAO_ADDR=https://192.168.70.12:8200 BAO_CACERT=/etc/openbao/ca.crt

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
