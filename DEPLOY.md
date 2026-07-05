# Déploiement minitel-gpt sur VPS Coolify (ESP32 + WebSocket)

Ce dépôt tourne en conteneur Docker (Flask + gunicorn), sans dépendance au
matériel Raspberry Pi de l'upstream d'origine.

## 1. Récupérer le dépôt

Fork ou clone direct de https://github.com/omarquet/minitel-gpt selon ton
usage.

## 2. Coolify

1. New Resource → Docker Compose (ou Dockerfile), source = ton fork.
2. Environment :
   - `LLM_PROVIDER=mistral` (ou `claude` / `gemini`)
   - `MISTRAL_KEY=...`  `MISTRAL_MODEL=mistral-small-latest`
   - `GEMINI_KEY=...`  `GEMINI_MODEL=gemini-2.0-flash` (si `LLM_PROVIDER=gemini`)
   - `ADMIN_PASSWORD=...`  `FLASK_SECRET=...`
   - `ADMIN_PUBLIC_URL=https://minitel.tondomaine.fr`
   - `WS_TOKEN=...` (recommandé) : sans ça, `/ws` est ouvert à qui connaît
     l'URL et peut consommer ta clé API. Avec un token, l'ESP32 et tes tests
     doivent ajouter `?token=...` à l'URL WebSocket.
3. Domaine `minitel.tondomaine.fr`, port exposé **8080** (Traefik gère TLS + WebSocket).
   ⚠️ Vérifie aussi le **port du healthcheck** dans les réglages Coolify : il
   revient parfois à une valeur par défaut (ex. 3000) qui ne correspond pas au
   port réel du conteneur (8080) - ça provoque un 502 permanent même si l'app
   tourne parfaitement (visible dans les logs : `Healthcheck URL: .../3000/healthz`).
4. Déploie.
   - Admin : `https://minitel.tondomaine.fr/`
   - Endpoint Minitel/ESP32 : `wss://minitel.tondomaine.fr/ws`

## 3. Tester sans ESP32

En local :
```bash
docker compose up --build
```
Ouvre `minitel-test.html`, URL `ws://localhost:8080/ws`, clique **Connecter**.
Tape une question, **Entrée** = ENVOI ; boutons SUITE / SOMMAIRE / GUIDE.

Une fois sur Coolify, pointe l'émulateur sur `wss://minitel.tondomaine.fr/ws`
pour valider le VPS avant toute soudure.

## 4. Matériel ESP32 (rappel critique)

Le port DIN du Minitel est en **5 V**, les GPIO de l'ESP32 en **3,3 V non
tolérants 5 V**. Un **adaptateur de niveau logique bidirectionnel** (BSS138 /
TXS0108E) est obligatoire entre les deux. Câblage détaillé dans le `.ino`.
