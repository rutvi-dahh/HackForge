#!/bin/sh
set -e

# Render's free plan has an ephemeral disk, so the database starts empty on every boot.
# Re-create demo users, policy, rates and planted receipts (they use the committed cache).
if [ "${SEED_ON_BOOT:-1}" = "1" ]; then
  python seed.py || echo "WARNING: seed.py failed, starting anyway"
fi

# 1 worker because SQLite; threads handle concurrent requests; long timeout for OCR + AI calls
exec gunicorn app:app \
  --bind 0.0.0.0:${PORT:-10000} \
  --workers 1 --threads 4 --timeout 120
