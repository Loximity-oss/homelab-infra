#!/bin/bash
set -euo pipefail
DIR=/var/backups/guac
KEEP=${GUAC_BACKUP_KEEP_DAYS:-7}
umask 077
f="$DIR/guacamole_db-$(date +%Y%m%d-%H%M%S).sql.gz"
podman exec guac-db pg_dump -U guacamole -d guacamole_db | gzip > "$f"
find "$DIR" -name 'guacamole_db-*.sql.gz' -mtime +"$KEEP" -delete
echo "backup ok: $f"
