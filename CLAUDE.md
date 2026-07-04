# CLAUDE.md — contexte projet minitel-gpt (fork omarquet)

Ce fichier est chargé automatiquement par Claude Code. Il résume l'objectif, ce
qui a été fait, et ce qu'il reste à faire, pour reprendre le travail dans VS Code.

## Objectif

Fork de `jherard-fr/minitel-gpt`. Le projet d'origine transforme un Minitel en
terminal de chat IA (Mistral ou Claude) piloté par un Raspberry Pi qui lit
directement le port série `/dev/ttyUSB0` (FTDI -> DIN5, 1200 7E1).

Ma variante déplace le service sur un **VPS Coolify** (image Docker) et remplace
le Pi + FTDI par un **ESP32** qui fait un pont transparent entre l'UART du
Minitel et le serveur via **WebSocket sécurisé (wss)**.

Chaîne cible :
```
Minitel --DIN5 1200 7E1--> ESP32 (UART) --WiFi wss://--> Coolify/Traefik --> conteneur minitel-gpt
```

## Ce qui a été fait (fichiers ajoutés, tous ADDITIFS)

- `services/server.py` — point d'entrée VPS. Un seul processus Flask qui sert
  l'admin existante sur `/` et un endpoint WebSocket `/ws`. Il réutilise toute la
  boucle de chat de `minitel_chatgpt.py` via une classe `WSTerm` qui a la même
  interface que `Term`, mais les octets Videotex circulent sur la WebSocket au
  lieu du port série. Ne modifie AUCUN fichier d'origine (git pull upstream OK).
- `Dockerfile` + `entrypoint.sh` + `requirements.txt` — image Python 3.11,
  lancée par gunicorn (`-k gthread`). L'entrypoint amorce le volume config.
- `docker-compose.yml` — pour Coolify, volume `minitel-config` persistant.
- `firmware/minitel_esp32_bridge.ino` — firmware ESP32 : UART2 en `SERIAL_7E1`
  (1200 bauds) <-> WebSocket client (lib WebSockets de Links2004). Relais brut.
- `minitel-emulator.html` — émulateur Minitel navigateur qui parle le MÊME
  protocole WebSocket binaire que l'ESP32 (rendu Videotex 40 col, touches SEP).
  Sert à tester SANS matériel.
- `DEPLOY.md` — guide de déploiement Coolify pas à pas.

## Points techniques importants / pièges

- Le protocole WS est symétrique : frames binaires, octets bruts dans les deux
  sens. Le serveur envoie du Videotex 7 bits, l'ESP32 relaie sans rien parser.
- Le 7E1 est géré par l'UART de l'ESP32, pas côté serveur.
- Transport = WebSocket (pas TCP brut) car Traefik/Coolify route le wss en 443
  sans config spéciale.
- **Piège matériel** : le port DIN Minitel est en 5 V, les GPIO ESP32 en 3,3 V
  NON tolérants 5 V -> adaptateur de niveau logique bidirectionnel OBLIGATOIRE
  (BSS138 / TXS0108E), au moins sur Minitel TX -> ESP32 RX.
- L'admin en conteneur : les boutons Update/Rollback/Restart ne marchent pas
  (pas de systemd) ; les mises à jour se font par redéploiement Coolify. Les
  personnalités/prompts se rechargent à chaud à chaque retour au sommaire ; la
  clé/le provider LLM viennent des variables d'env (redéploiement pour changer).

## Variables d'environnement (Coolify)

`LLM_PROVIDER`, `MISTRAL_KEY`, `MISTRAL_MODEL`, `ANTHROPIC_KEY`, `CLAUDE_MODEL`,
`ADMIN_PASSWORD`, `FLASK_SECRET`, `ADMIN_PUBLIC_URL`.

## Statut actuel

- [x] Refactor transport + `server.py` validé (import + session simulée + stack
      réel gunicorn/flask-sock testés : accueil, pagination, touches OK).
- [x] Fork créé : https://github.com/omarquet/minitel-gpt
- [ ] Fichiers poussés sur le fork (git add/commit/push).
- [ ] Test local `docker compose up --build` + émulateur.
- [ ] Déploiement Coolify (domaine + port 8080 + variables d'env).
- [ ] Montage matériel ESP32 + level shifter, flash du firmware.

## Prochaines pistes possibles

- Reconnexion / gestion de plusieurs Minitels simultanés côté serveur.
- Durcir le wss côté ESP32 (empreinte du certificat).
- Adapter la touche GUIDE pour afficher `ADMIN_PUBLIC_URL`.
