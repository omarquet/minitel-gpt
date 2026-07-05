# MINITEL GPT

Transformer un **Minitel** en terminal de chat IA, servi par un conteneur
Docker hébergé sur un VPS (testé avec **Coolify**), relié au Minitel par un
**ESP32** qui fait le pont entre le port série (DIN5, Vidéotex) et une
**WebSocket sécurisée (wss)**.

Fork de [jherard-fr/minitel-gpt](https://github.com/jherard-fr/minitel-gpt),
qui pilote le Minitel directement depuis un Raspberry Pi (port série local).
Ce fork remplace entièrement le Pi et le port série par la chaîne suivante :

```
Minitel --DIN5 1200 7E1--> ESP32 (UART) --WiFi wss://--> Coolify/Traefik --> conteneur minitel-gpt
```

On tape sa question sur le clavier du Minitel, le serveur interroge le modèle
d'IA choisi - **Mistral**, **Claude** ou **Gemini** - et affiche la réponse à
l'écran, à 1200 bauds. Le fournisseur, la clé et le modèle se règlent depuis
l'interface web, tout comme la personnalité de l'assistant (la version phare
est « bloquée dans les années 80 »).

---

## Déploiement

Voir [DEPLOY.md](DEPLOY.md) pour la procédure pas à pas (fork GitHub, Coolify,
variables d'environnement, test sans matériel).

Variables d'environnement principales (`.env.example` à la racine) :

| Variable | Rôle |
|---|---|
| `LLM_PROVIDER` | `mistral` (défaut), `claude` ou `gemini` |
| `MISTRAL_KEY`, `MISTRAL_MODEL` | Clé et modèle Mistral |
| `ANTHROPIC_KEY`, `CLAUDE_MODEL` | Clé et modèle Claude |
| `GEMINI_KEY`, `GEMINI_MODEL` | Clé et modèle Gemini |
| `ADMIN_PASSWORD` | Mot de passe de l'admin web |
| `FLASK_SECRET` | Clé de signature des sessions Flask (mets une vraie valeur aléatoire en prod) |
| `ADMIN_PUBLIC_URL` | URL admin affichée sur le Minitel via la touche GUIDE |
| `WS_TOKEN` | Jeton requis en `?token=...` pour se connecter à `/ws` (voir [Sécurité](#sécurité)) |

## Tester sans matériel

Ouvre `https://<ton-domaine>/minitel-test.html` : un émulateur Minitel dans le
navigateur qui parle le même protocole WebSocket binaire que l'ESP32 (rendu
Vidéotex 40 colonnes, boutons pour les touches de fonction). L'URL WebSocket
et le jeton (`WS_TOKEN`) sont saisissables dans l'interface.

## Sécurité

`/ws` (et les endpoints de test `/ws-echo`, `/ws-gemini`) n'ont **aucune
authentification par défaut** : n'importe qui connaissant l'URL publique peut
discuter avec l'assistant et consommer ta clé API. Configure `WS_TOKEN` (une
valeur aléatoire, ex. `python3 -c "import secrets; print(secrets.token_hex(32))"`)
pour exiger `?token=...` sur la connexion WebSocket. L'ESP32 doit alors inclure
le même jeton dans `WS_PATH` (voir le firmware).

L'écran affiché sur la touche **GUIDE** ne montre que l'URL de l'admin, jamais
le mot de passe (contrairement au Pi d'origine, où seul le foyer avait un
accès physique au Minitel - ici `/ws` est public). Il permet aussi de changer
de personnalité active directement depuis le Minitel.

---

## Matériel (ESP32)

| Élément | Détail |
|---|---|
| ESP32 | N'importe quelle carte de dev (WiFi + 2 UART) |
| Adaptateur de niveau logique | **Bidirectionnel obligatoire** (BSS138 / TXS0108E) |
| Minitel | Prise DIN 5 broches « péri-informatique » |

> ⚠️ **Piège matériel** : le port DIN du Minitel est en **5 V**, les GPIO de
> l'ESP32 en **3,3 V non tolérants 5 V**. Un adaptateur de niveau logique
> bidirectionnel est **obligatoire**, au moins sur Minitel TX → ESP32 RX (sinon
> tu grilles le GPIO). Recommandé aussi dans l'autre sens pour une marge propre.

### Câblage

| DIN Minitel | ESP32 (via level shifter) | Rôle |
|---|---|---|
| broche 1 | UART2 TX (GPIO17) | ESP32 → Minitel (RX) |
| broche 3 | UART2 RX (GPIO16) | Minitel → ESP32 (TX) |
| broche 2 | GND | masse commune |
| broches 4 et 5 | **ne pas toucher** | la broche 5 porte une tension |

Paramètres série : **1200 bauds, 7 bits, parité paire, 1 stop (7E1)** - norme
Videotex, gérés par l'UART de l'ESP32 (`SERIAL_7E1`).

### Firmware

`firmware/minitel_esp32_bridge.ino` - relais transparent octet à octet entre
l'UART du Minitel et une connexion WebSocket cliente (lib **WebSockets** de
Markus Sattler / Links2004, disponible dans le gestionnaire de bibliothèques
Arduino). À configurer avant flash : SSID/mot de passe WiFi, domaine du
serveur (`WS_HOST`), et le jeton `WS_TOKEN` dans `WS_PATH` si configuré côté
serveur.

---

## Interface d'administration

`https://<ton-domaine>/` - mot de passe défini par `ADMIN_PASSWORD`.

- **Personnalités** : créer / modifier / supprimer des presets, prompt système
  en éditeur multi-lignes, génération de prompt par IA, **fichiers de
  connaissance** (.txt) injectés dans le contexte, textes d'accueil
  personnalisables, zone de test sans le Minitel.
- **Paramètres** : choix du **fournisseur d'IA** (Mistral, Claude) avec la clé
  et le modèle de chacun, logs. (Gemini se configure uniquement par variable
  d'environnement pour l'instant, pas encore dans ce formulaire.)

Les personnalités sont stockées dans `config/prompts.json` (créé au premier
démarrage depuis `config/prompts.default.json`, non versionné), leurs fichiers
de connaissance dans `config/knowledge/<personnalité>/` - tout ça vit dans le
volume Docker persistant, pas dans le dépôt git.

> Les personnalités/prompts sont pris en compte immédiatement (relus à chaque
> conversation). En revanche, changer la clé/le fournisseur LLM dans
> **Paramètres** ne s'applique au terminal Minitel qu'après un redéploiement
> Coolify (les mises à jour de code aussi passent par un redéploiement après
> `git push`, plus de bouton "mettre à jour" dans l'admin).

---

## Touches du Minitel

| Touche | Effet |
|---|---|
| **ENVOI** | Envoie la question tapée |
| **SOMMAIRE** | Retour au menu principal (recharge le preset, réinitialise la conversation) |
| **GUIDE** | Change de personnalité active (touche numérique), affiche aussi l'URL de l'admin |
| **RETOUR** / **CORRECTION** | Efface le dernier caractère tapé. Pendant la lecture d'une réponse multi-pages, RETOUR revient à la page précédente (autant de fois que nécessaire). Une fois la question suivante affichée (saisie vide), RETOUR rouvre la dernière réponse depuis sa dernière page - SUITE là-dedans termine la révision |
| **ANNULATION** | Efface toute la phrase en cours de saisie |
| **REPETITION** | Réaffiche la dernière réponse de l'assistant (sans nouvel appel API) |
| **SUITE** | Page suivante (réponse sur plusieurs écrans) |

---

## Arborescence

```
services/
  server.py             point d'entrée VPS : admin + WebSocket /ws
  minitel_gpt.py        écrans, lecture clavier, appel LLM (Mistral/Claude/Gemini)
  admin_ui.py           interface web d'admin
config/
  prompts.default.json  personnalités par défaut (prompts.json créé au 1er boot)
firmware/
  minitel_esp32_bridge.ino  pont UART <-> WebSocket
minitel-test.html        émulateur Minitel dans le navigateur (test sans matériel)
Dockerfile, docker-compose.yml, entrypoint.sh
DEPLOY.md                procédure de déploiement Coolify
```

---

## Dépannage

| Symptôme | Cause probable |
|---|---|
| 502 Bad Gateway sur toutes les routes | conteneur pas démarré/joignable - vérifie les logs Coolify et le port du healthcheck (doit être celui de `EXPOSE` dans le Dockerfile, pas un défaut générique) |
| `minitel-test.html` ne se connecte pas | mauvais protocole (`wss://` pas `ws://` derrière Traefik/HTTPS), `WS_TOKEN` manquant dans l'URL si configuré côté serveur, ou pas de port dans l'URL publique (Traefik route en 443 en interne vers le port du conteneur) |
| Erreur API (401/403) sur les réponses | clé du fournisseur actif (`LLM_PROVIDER`) absente ou invalide |
| Rien ne s'affiche sur le vrai Minitel | câblage DIN délogé, level shifter absent/mal branché, ou broche 4/5 pontée par erreur |
| Charabia à l'écran | vitesse ESP32 ≠ vitesse Minitel (rester à 1200 bauds 7E1 des deux côtés) |

---

*Projet personnel de Jérôme Hérard pour la version originale Raspberry Pi.*
