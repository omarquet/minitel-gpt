#!/bin/sh
set -e

mkdir -p /app/config /app/logs

# Amorce les fichiers de config par defaut dans le volume persistant,
# SANS ecraser ce qui existe deja (personnalites, connaissances uploadees...).
if [ -d /app/seed-config ]; then
  cp -rn /app/seed-config/. /app/config/ 2>/dev/null || true
fi

# Cree prompts.json a partir du defaut au tout premier lancement.
if [ ! -f /app/config/prompts.json ] && [ -f /app/config/prompts.default.json ]; then
  cp /app/config/prompts.default.json /app/config/prompts.json
fi

exec "$@"
