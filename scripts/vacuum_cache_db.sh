#!/usr/bin/env bash
set -euo pipefail

DB_PATH="/home/free/agents/cache/intel_cache.db"

if [[ ! -f "$DB_PATH" ]]; then
  echo "[!] Target database $DB_PATH not found. Skipping maintenance."
  exit 0
fi

echo "[*] Starting maintenance on $DB_PATH..."
BEFORE_SIZE=$(stat -c %s "$DB_PATH")

# Truncate WAL to flush pending write-ahead pages into the DB file, then vacuum
sqlite3 "$DB_PATH" "PRAGMA wal_checkpoint(TRUNCATE); VACUUM; PRAGMA optimize;"

AFTER_SIZE=$(stat -c %s "$DB_PATH")
echo "[+] Maintenance complete. Size before: ${BEFORE_SIZE} bytes | Size after: ${AFTER_SIZE} bytes."
