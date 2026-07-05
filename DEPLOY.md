# Déploiement minitel-gpt sur VPS Coolify (ESP32 + WebSocket)

Ces fichiers s'ajoutent au dépôt d'origine `jherard-fr/minitel-gpt`. Ils sont
**additifs** : ils ne modifient aucun fichier existant, donc le `git pull` de
l'upstream reste possible.

## 1. Forker le dépôt (30 s, sur GitHub)

1. Va sur https://github.com/jherard-fr/minitel-gpt
2. Clique **Fork** (en haut à droite) puis **Create fork**.
3. Ton fork est sur `https://github.com/TON-PSEUDO/minitel-gpt`.

## 2. Déposer ces fichiers dans le fork

En ligne de commande :

```bash
git clone https://github.com/TON-PSEUDO/minitel-gpt.git
cd minitel-gpt
# décompresse l'archive des ajouts ICI (à la racine) : les fichiers
# atterrissent aux bons endroits (services/server.py, Dockerfile, etc.)
git add .
git commit -m "Déploiement VPS Coolify : bridge WebSocket ESP32 + Docker"
git push
```

Emplacements attendus :
- `services/server.py`
- `Dockerfile`, `entrypoint.sh`, `requirements.txt`, `docker-compose.yml` (racine)
- `firmware/minitel_esp32_bridge.ino`
- `minitel-test.html` (racine, sert au test navigateur)

## 3. Coolify

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
4. Déploie.
   - Admin : `https://minitel.tondomaine.fr/`
   - Endpoint Minitel/ESP32 : `wss://minitel.tondomaine.fr/ws`

## 4. Tester sans ESP32

En local :
```bash
docker compose up --build
```
Ouvre `minitel-test.html`, URL `ws://localhost:8080/ws`, clique **Connecter**.
Tape une question, **Entrée** = ENVOI ; boutons SUITE / SOMMAIRE / GUIDE.

Une fois sur Coolify, pointe l'émulateur sur `wss://minitel.tondomaine.fr/ws`
pour valider le VPS avant toute soudure.

## 5. Matériel ESP32 (rappel critique)

Le port DIN du Minitel est en **5 V**, les GPIO de l'ESP32 en **3,3 V non
tolérants 5 V**. Un **adaptateur de niveau logique bidirectionnel** (BSS138 /
TXS0108E) est obligatoire entre les deux. Câblage détaillé dans le `.ino`.
