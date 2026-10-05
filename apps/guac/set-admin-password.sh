#!/bin/bash
# Set the guacadmin password from /etc/guac/secrets/admin-password (idempotent via hash marker).
# Guacamole hash: SHA-256(password || UPPER(hex(salt))).
set -euo pipefail
PWFILE=/etc/guac/secrets/admin-password
MARK=/var/lib/guac/admin-password.sha256
cur=$(sha256sum "$PWFILE" | cut -d' ' -f1)
if [ -f "$MARK" ] && [ "$(cat "$MARK")" = "$cur" ]; then
  echo "unchanged"; exit 0
fi
PW=$(cat "$PWFILE")
SALT=$(openssl rand -hex 32)
export PW SALT
podman exec -i -e PW -e SALT guac-db psql -U guacamole -d guacamole_db -v ON_ERROR_STOP=1 <<'SQL'
\getenv pw PW
\getenv salt SALT
UPDATE guacamole_user SET
  password_salt = decode(:'salt', 'hex'),
  password_hash = sha256(convert_to(:'pw' || upper(:'salt'), 'UTF8')),
  password_date = now()
WHERE entity_id = (SELECT entity_id FROM guacamole_entity WHERE name = 'guacadmin' AND type = 'USER');
SQL
umask 077
echo "$cur" > "$MARK"
echo "CHANGED"
