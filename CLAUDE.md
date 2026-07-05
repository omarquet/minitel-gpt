# CLAUDE.md — contexte projet minitel-gpt (fork omarquet)

Ce fichier est chargé automatiquement par Claude Code. Il résume l'objectif, ce
qui a été fait, et ce qu'il reste à faire, pour reprendre le travail dans VS Code.

## Objectif

Fork de `jherard-fr/minitel-gpt`. Le projet d'origine transforme un Minitel en
terminal de chat IA (Mistral ou Claude) piloté par un Raspberry Pi qui lit
directement le port série `/dev/ttyUSB0` (FTDI -> DIN5, 1200 7E1).

Ce fork déplace le service sur un **VPS Coolify** (image Docker) et remplace
le Pi + FTDI par un **ESP32** qui fait un pont transparent entre l'UART du
Minitel et le serveur via **WebSocket sécurisé (wss)**. Le support Raspberry Pi
d'origine (port série, systemd, WiFi captif) a été **entièrement retiré** :
ce n'est plus un simple ajout par-dessus l'upstream, `minitel_chatgpt.py` a
été nettoyé de son code Pi. Resynchroniser avec l'upstream (`git pull`) n'est
donc plus possible sans conflits.

Chaîne cible :
```
Minitel --DIN5 1200 7E1--> ESP32 (UART) --WiFi wss://--> Coolify/Traefik --> conteneur minitel-gpt
```

## Répartition du code

- `services/minitel_chatgpt.py` — logique partagée : écrans Videotex, lecture
  clavier (`read_question`, gère aussi ANNULATION/REPETITION), pagination des
  réponses, et `call_llm()` qui aiguille vers Mistral/Claude/Gemini selon
  `LLM_PROVIDER`. Ne contient plus rien de spécifique au Pi (le port série et
  la classe `Term` d'origine ont été supprimés).
- `services/server.py` — point d'entrée VPS. Un seul processus Flask qui sert
  l'admin existante sur `/` et un endpoint WebSocket `/ws`. Réutilise les
  fonctions de `minitel_chatgpt.py` via une classe `WSTerm` (même interface
  `w`/`clear`/`line`/`center`/`read_byte`/`read_key`), mais les octets Videotex
  circulent sur la WebSocket. Ajoute aussi `/ws-echo` et `/ws-gemini` (test),
  la protection `WS_TOKEN`, l'injection de la date réelle (`fixed_year`), et
  l'accès web live pour Agile en Seine 2026 (seule exception à la coupure de
  connaissances post-1989, voir `config/prompts.default.json`).
- `Dockerfile` + `entrypoint.sh` + `requirements.txt` — image Python 3.11,
  lancée par gunicorn (`-k gthread`). L'entrypoint amorce le volume config.
- `docker-compose.yml` — pour Coolify, volume `minitel-config` persistant.
- `firmware/minitel_esp32_bridge.ino` — firmware ESP32 : UART2 en `SERIAL_7E1`
  (1200 bauds) <-> WebSocket client (lib WebSockets de Links2004). Relais brut.
- `minitel-test.html` — émulateur Minitel navigateur qui parle le MÊME
  protocole WebSocket binaire que l'ESP32 (rendu Videotex 40 col, touches SEP),
  URL et token WS configurables dans l'interface. Sert à tester SANS matériel.
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
- **Sécurité `/ws`** : par défaut, aucune authentification — n'importe qui
  connaissant l'URL publique peut discuter et consommer la clé API. Si
  `WS_TOKEN` est configuré côté serveur, `/ws`, `/ws-echo` et `/ws-gemini`
  exigent `?token=...` en query string (sinon connexion refusée en silence).
  L'ESP32 doit inclure le même token dans `WS_PATH` (voir le `.ino`).

## Variables d'environnement (Coolify)

`LLM_PROVIDER` (`mistral`, `claude` ou `gemini`), `MISTRAL_KEY`, `MISTRAL_MODEL`,
`ANTHROPIC_KEY`, `CLAUDE_MODEL`, `GEMINI_KEY`, `GEMINI_MODEL`,
`ADMIN_PASSWORD`, `FLASK_SECRET`, `ADMIN_PUBLIC_URL`, `WS_TOKEN`.

## Statut actuel

- [x] Refactor transport + `server.py` validé (import + session simulée + stack
      réel gunicorn/flask-sock testés : accueil, pagination, touches OK).
- [x] Fork créé : https://github.com/omarquet/minitel-gpt, déployé sur Coolify
      (https://minitel.playground.aqoba.fr).
- [x] Support Raspberry Pi entièrement retiré (install.sh, unités systemd,
      sudoers, port série, WiFi captif) ; README.md réécrit pour VPS/ESP32.
- [x] Gemini consolidé dans `minitel_chatgpt.py` (plus dupliqué dans `server.py`).
- [ ] Montage matériel ESP32 + level shifter, flash du firmware.

## Prochaines pistes possibles

- Reconnexion / gestion de plusieurs Minitels simultanés côté serveur.
- Durcir le wss côté ESP32 (empreinte du certificat).
- Support Gemini dans le formulaire de l'admin web (actuellement env var only).
