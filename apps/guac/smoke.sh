#!/bin/bash
# Smoke test for hm1-dmz-guac01. Exit non-zero on any core failure.
fail=0
ok()  { echo "OK   $*"; }
bad() { echo "FAIL $*"; fail=1; }

for u in guac-db guac-guacd guac-web; do
  systemctl is-active --quiet "$u" && ok "$u active" || bad "$u not active"
done

code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/)
[ "$code" = "200" ] && ok "web answers on 127.0.0.1:8080 (HTTP $code)" || bad "web HTTP $code"

# Default guacadmin/guacadmin hash must be gone
def=$(podman exec guac-db psql -U guacamole -d guacamole_db -tA -c \
 "select bool_or(password_hash = sha256(convert_to('guacadmin' || upper(encode(password_salt,'hex')),'UTF8'))) from guacamole_user u join guacamole_entity e using (entity_id) where e.name='guacadmin'" 2>&1)
[ "$def" = "f" ] && ok "default admin password replaced" || bad "default admin password check: '$def'"

# Nothing listening on the LAN address
if ss -ltn | awk '{print $4}' | grep -Eq '^(0\.0\.0\.0|\*|\[::\]|192\.168\.71\.10):(8080|2000|4822|5432)$'; then
  bad "service listening beyond loopback"
else
  ok "no service exposed on LAN"
fi

if [ -f /etc/guac/secrets/cloudflared.env ]; then
  systemctl is-active --quiet guac-cloudflared && ok "guac-cloudflared active" || bad "guac-cloudflared not active"
  c=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:2000/ready)
  [ "$c" = "200" ] && ok "tunnel connected (/ready 200)" || bad "tunnel /ready HTTP $c"
else
  echo "SKIP cloudflared: token not provisioned (expected until cloudflared-guac01-tunnel is set)"
fi
exit $fail
