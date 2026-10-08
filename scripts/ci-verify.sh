#!/usr/bin/env bash
# Nightly / on-demand guest verification (decision 32). Issues "verify: <host>" (label verify): opened or commented
# when a host fails, closed when it is green again. Exit 1 on any failure -> pipeline.failed (workflow verify) -> SRE.
BASE=$(cd "$(dirname "$0")/.." && pwd)
source "$BASE/scripts/ci-lib.sh"
export LC_ALL=C.UTF-8
bao_login ci-apply
export GITEA_API_TOKEN; GITEA_API_TOKEN=$(secret plan gitea_ci_token)
PLAN_PVE_TOKEN=$(secret plan pve_token_plan); APPLY_TOKEN=$(secret apply pve_token_apply)
bao_logout
W=$(mktemp -d); STD="$W/standards"
clone homelab/homelab-standards "$STD" "$GITEA_API_TOKEN"
if [ -n "${HOST:-}" ]; then SEL=(--hosts "$HOST"); else SEL=(--all); fi
ok=0; PROXMOX_VE_API_TOKEN="$PLAN_PVE_TOKEN" INFRA_ROOT="$BASE" python3 "$BASE/scripts/verify.py" --standards "$STD" "${SEL[@]}" --out "$W/v" > "$W/verify.log" 2>&1 || ok=$?
[ -f "$W/v/verify.json" ] || { echo "verify did not run:"; tail -30 "$W/verify.log"; exit 1; }
cat "$W/v/verify.md"
# one issue per failing host; close the issue of hosts that are green again
INFRA_ROOT="$BASE" python3 - "$W/v/verify.json" "$W" <<'PY'
import json, sys, subprocess, os
rows = json.load(open(sys.argv[1]))["rows"]; w = sys.argv[2]
icon = {"pass": "✅", "fail": "❌", "skip": "⚠️"}
for h in sorted({r["host"] for r in rows}):
    mine = [r for r in rows if r["host"] == h]; bad = [r for r in mine if r["status"] == "fail"]
    title = f"verify: {h}"
    if bad:
        f = os.path.join(w, f"{h}.issue.md")
        with open(f, "w") as o:
            o.write(f"**Nightly verification failed** for `{h}`: {len(bad)} of {len(mine)} checks.\n\n| Layer | Check | | Evidence |\n|---|---|---|---|\n")
            for r in bad:
                o.write(f"| {r['layer']} | {r['check']} | {icon[r['status']]} | {r['evidence'].replace('|', '/')} |\n")
            o.write("\nRe-test one host: Gitea → Actions → verify → Run workflow (host).\n")
        subprocess.run(["python3", "scripts/gitea.py", "issue-upsert", title, "verify", f], cwd=os.environ["INFRA_ROOT"], check=False)
    else:
        subprocess.run(["python3", "scripts/gitea.py", "issue-close", title, "Green again: all checks passed."], cwd=os.environ["INFRA_ROOT"], check=False)
PY
if [ -z "${HOST:-}" ]; then
  echo "== prune pre-apply_* snapshots older than 7 days"
  PROXMOX_VE_API_TOKEN="$APPLY_TOKEN" INFRA_ROOT="$BASE" python3 "$BASE/scripts/snapshot.py" prune --days 7 || echo "prune: some snapshots not deleted"
fi
exit $ok
