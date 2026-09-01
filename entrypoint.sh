#!/bin/sh
set -e

mkdir -p /app/config /app/logs

if [ -d /app/seed-config ]; then
  # Donnees de l'utilisateur (prompts.json, knowledge/, llm.json) : amorcees au
  # premier lancement, JAMAIS ecrasees ensuite.
  cp -rn /app/seed-config/. /app/config/ 2>/dev/null || true

  # Fichiers de reference fournis par le depot : toujours remis a jour, ce sont
  # du code et non des donnees. Sans ca, le cp -rn ci-dessus les gelait a leur
  # version du premier deploiement : une personnalite ajoutee au depot
  # n'arrivait jamais (ensure_prompts fusionnait depuis un defaut perime) et
  # modifier un prompt .txt n'avait aucun effet en production, alors que c'est
  # tout l'interet du couple prompt_file / resolve_prompt.
  cp -f /app/seed-config/prompts.default.json /app/config/ 2>/dev/null || true
  mkdir -p /app/config/prompts
  cp -f /app/seed-config/prompts/*.txt /app/config/prompts/ 2>/dev/null || true
fi

# Cree prompts.json a partir du defaut au tout premier lancement.
if [ ! -f /app/config/prompts.json ] && [ -f /app/config/prompts.default.json ]; then
  cp /app/config/prompts.default.json /app/config/prompts.json
fi

exec "$@"
