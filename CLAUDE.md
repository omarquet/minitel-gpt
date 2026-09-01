# CLAUDE.md — contexte projet minitel-gpt (fork omarquet)

Ce fichier est chargé automatiquement par Claude Code. Il résume l'objectif, ce
qui a été fait, et ce qu'il reste à faire, pour reprendre le travail dans VS Code.

## Objectif

Fork de `jherard-fr/minitel-gpt`. Le projet d'origine transforme un Minitel en
terminal de chat IA (Mistral ou Claude) piloté par un Raspberry Pi qui lit
directement le port série `/dev/ttyUSB0` (FTDI -> DIN5, 1200 7E1).

Ce fork déplace le service sur un **VPS** (image Docker) et remplace
le Pi + FTDI par un **ESP32** qui fait un pont transparent entre l'UART du
Minitel et le serveur via **WebSocket sécurisé (wss)**. Le support Raspberry Pi
d'origine (port série, systemd, WiFi captif) a été **entièrement retiré** :
ce n'est plus un simple ajout par-dessus l'upstream, `minitel_gpt.py` a
été nettoyé de son code Pi. Resynchroniser avec l'upstream (`git pull`) n'est
donc plus possible sans conflits.

Chaîne cible :
```
Minitel --DIN5 1200 7E1--> ESP32 (UART) --WiFi wss://--> reverse proxy --> conteneur minitel-gpt
```

## Répartition du code

- `services/minitel_gpt.py` — logique partagée : écrans Videotex, lecture
  clavier (`read_question`, gère aussi ANNULATION/REPETITION), pagination des
  réponses, et `call_llm()` qui aiguille vers Mistral/Claude/Gemini selon
  `LLM_PROVIDER`. Ne contient plus rien de spécifique au Pi (le port série et
  la classe `Term` d'origine ont été supprimés).
- `services/server.py` — point d'entrée VPS. Un seul processus Flask qui sert
  l'admin existante sur `/` et un endpoint WebSocket `/ws`. Réutilise les
  fonctions de `minitel_gpt.py` via une classe `WSTerm` (même interface
  `w`/`clear`/`line`/`center`/`read_byte`/`read_key`), mais les octets Videotex
  circulent sur la WebSocket. Ajoute aussi `/ws-echo` et `/ws-gemini` (test),
  la protection `WS_TOKEN`, l'injection de la date réelle (`fixed_year`), et
  l'accès web live pour Agile en Seine 2026 (seule exception à la coupure de
  connaissances post-1989, voir `config/prompts.default.json`).
  `show_guide_ws()` (touche GUIDE) permet aussi de changer de personnalité
  active directement depuis le Minitel (liste numérotée, écrit `data["active"]`
  dans `prompts.json`), en plus d'afficher l'URL de l'admin.
  Sert enfin la dictée depuis un téléphone : `/dictee` (la page), `/dictee/status`
  et `/dictee/inject`, adossés au registre `SESSIONS` des sessions ouvertes.
- `Dockerfile` + `entrypoint.sh` + `requirements.txt` — image Python 3.11,
  lancée par gunicorn (`-k gthread`). L'entrypoint amorce le volume config, et
  y **remet à jour à chaque démarrage** les fichiers de référence du dépôt
  (`prompts.default.json`, `prompts/*.txt`) sans jamais toucher aux données
  (`prompts.json`, `knowledge/`, `llm.json`).
- `docker-compose.yml` — pour le serveur, volume `minitel-config` persistant.
- `firmware/firmware.ino` — firmware ESP32-C3 : UART1 (GPIO4 RX / GPIO5 TX) en
  `SERIAL_7E1` (1200 bauds) <-> WebSocket client (lib WebSockets de Links2004).
  Relais brut. Le C3 n'a pas d'UART2 et ses GPIO11-17 sont pris par la flash :
  les GPIO16/17 du projet d'origine sont inutilisables. Identifiants WiFi et
  token WS dans `firmware/secrets.h` (ignoré par git, modèle `secrets.h.example`).
- `minitel.html` — émulateur Minitel navigateur qui parle le MÊME
  protocole WebSocket binaire que l'ESP32 (rendu Videotex 40 col, touches SEP),
  URL et token WS configurables dans l'interface. Sert à tester SANS matériel.
- `dictee.html` — page servie sur `/dictee` (et `/dictee.html`), pensée pour Safari iOS :
  on dicte dans un `<textarea>` avec le micro du clavier natif (l'API
  SpeechRecognition n'est PAS utilisée, mal supportée sur iOS) et le texte
  s'écrit sur le Minitel dans la session en cours. Deux modes (au fil de la
  dictée / relire puis envoyer), boutons ENVOI et Effacer. Un seul fichier,
  aucune dépendance externe. Le jeton se saisit une fois (ou une seule fois par
  `?token=...`, aussitôt effacé de la barre d'adresse via `history.replaceState`)
  puis vit dans le `localStorage` du téléphone : il n'est visible nulle part,
  l'appareil étant fait pour passer de main en main. Un 403 le fait oublier,
  sinon il rejouerait le refus à chaque ouverture. Bouton « Oublier le jeton ».
- `DEPLOY.md` — guide de déploiement pas à pas.

## Points techniques importants / pièges

- Le protocole WS est symétrique : frames binaires, octets bruts dans les deux
  sens. Le serveur envoie du Videotex 7 bits, l'ESP32 relaie sans rien parser.
- Le 7E1 est géré par l'UART de l'ESP32, pas côté serveur.
- Transport = WebSocket (pas TCP brut) car le reverse proxy route le wss en 443
  sans config spéciale.
- **Piège matériel** : le port DIN Minitel est en 5 V, les GPIO ESP32 en 3,3 V
  NON tolérants 5 V -> adaptateur de niveau logique bidirectionnel OBLIGATOIRE
  (BSS138 / TXS0108E), au moins sur Minitel TX -> ESP32 RX.
- **Piège matériel n°2 — 12 V sur la broche 5 du DIN** (mesuré sur le Minitel 2
  Alcatel du montage). Ne jamais la relier à l'ESP32-C3 : sur ces petites
  cartes, `5V` est câblée au VBUS USB et le régulateur 3,3 V plafonne vers 6 V.
  Pour un montage autonome : buck DC-DC 12 V -> 5 V (tension vérifiée au
  voltmètre) + condensateur 470-1000 µF, et ne jamais cumuler l'USB et le buck
  sur le même rail. Détail complet dans l'en-tête de `firmware/firmware.ino`.
- **Ordre de branchement** : USB d'abord, DIN ensuite (et l'inverse pour
  débrancher). Le VB du TXS0108E vient de la broche `5V` de l'ESP32-C3, qui est
  le VBUS USB : DIN branché en premier, le 5 V du Minitel remonte par les diodes
  de protection et alimente partiellement la carte (LED d'alim faiblement
  allumée, rails indéterminés).
- **Montage de référence** : ESP32-C3 (UART1, GPIO4 RX / GPIO5 TX), TXS0108E
  VA=3,3 V / VB=5 V avec OE tiré haut par la carte, alimenté en USB pendant la
  mise au point. LED de statut sur GPIO8, **logique inversée** (`LOW` = allumée) :
  flash bref toutes les 2 s = tout va bien, clignotement lent = WebSocket coupée,
  rapide = WiFi perdu, LED figée = `loop()` bloqué.
- L'admin en conteneur : les boutons Update/Rollback/Restart hérités du Pi
  (systemd + git) ont été retirés de l'interface (inutilisables en conteneur,
  cf. `git log` sur `admin_ui.py`) ; les mises à jour se font par redéploiement
  du serveur. Les personnalités/prompts se rechargent à chaud à chaque retour au
  sommaire, et depuis `/save-llm` le fournisseur, le modèle et les clés aussi :
  pas de redémarrage nécessaire.
- **Réglages du LLM : `config/llm.json`, pas `.env`** (piège corrigé, à ne pas
  refaire). `/save-llm` écrivait dans `.env`, et le terminal ne voyait JAMAIS le
  changement, pour deux raisons cumulées : `.env` est à la racine de l'image
  (`/app/.env`), donc perdu au redéploiement puisque seul `/app/config` est un
  volume ; et `load_dotenv()` n'écrase pas une variable déjà présente dans
  l'environnement, or `docker-compose.yml` en fournit toujours une, avec un
  défaut (`LLM_PROVIDER=${LLM_PROVIDER:-mistral}`) ou vide pour les clés.
  L'admin, lui, relisait `.env` à chaque appel : son test de personnalité
  utilisait donc un modèle que le Minitel n'utilisait pas, sans le moindre
  avertissement, et le message de confirmation affirmait faussement qu'un
  redéploiement suffirait. Désormais `mg.llm_settings()` est la seule source :
  `config/llm.json` (volume, gitignoré, écrit atomiquement par l'admin) >
  variable d'environnement > défaut du code, relu à **chaque** appel LLM.
  Corollaire à connaître : une valeur enregistrée dans l'admin gagne
  définitivement sur les variables d'environnement du serveur ; pour revenir à
  celles-ci, il faut retirer la clé du JSON (pas d'action dans l'UI), comme
  pour le champ `prompt` des personnalités.
- **Le modèle replie ses lignes malgré la consigne** : le prompt lui interdit
  d'insérer des retours à la ligne (`MINITEL_MARKUP_HELP`, et deux tentatives de
  reformulation : `e247ced`, `cc2bb3e`), il le fait quand même, autour de 40
  caractères. `wrap()` traitant chaque `\n` comme une fin de paragraphe, le mot
  qui dépassait se retrouvait seul sur sa ligne (vu à l'écran : `...Marie-`
  `Antoinette y` / `y` / `fut enfermée...`). `join_soft_wraps()` recolle donc
  les lignes qui sont un repli subi, avant le découpage. Ne PAS recoller une
  mise en page voulue : ligne vide, bloc `{art}`, titre en `{grand}` ou en
  capitales, élément de liste, ligne « Adresse : ... », ou fin de phrase
  (ponctuation finale). Le garde-fou décisif est la longueur : une ligne de
  moins de `SOFT_WRAP_MIN_COLS` colonnes a été coupée volontairement, une ligne
  pleine est un repli. Sans le garde-fou sur les labels, deux lignes
  « Horaires : ... » / « Tarif : ... » sans point final fusionnaient.
- **Volume : données contre fichiers de référence** (piège corrigé, il a coûté
  deux allers-retours). `entrypoint.sh` amorçait tout le volume en `cp -rn`
  (no-clobber), y compris `prompts.default.json` et `prompts/*.txt`, qui sont
  du **code** et non des données. Conséquence : ces deux-là restaient gelés à
  leur version du **premier** déploiement. Une personnalité ajoutée au dépôt
  n'apparaissait donc jamais, même après le correctif de fusion
  (`ensure_prompts()` comparait avec un défaut périmé), et modifier un prompt
  `.txt` n'avait aucun effet en production, alors que c'est tout l'intérêt du
  couple `prompt_file` / `resolve_prompt()`. Seuls les fichiers réellement
  nouveaux arrivaient. L'entrypoint réécrit maintenant ces fichiers de
  référence à chaque démarrage, et continue de ne jamais toucher à
  `prompts.json`, `knowledge/` ni `llm.json`. Règle à appliquer pour tout
  nouveau fichier de `config/` : donnée de l'utilisateur -> `cp -rn` ;
  fourni par le dépôt -> `cp -f`.
- **Titre d'accueil sur deux lignes** : `title_msg2`, facultatif, s'affiche
  juste sous `title_msg`, même couleur. `load_preset()` les rend joints par un
  `\n` dans la même case du tuple et `show_home()` découpe : pas de signature
  à changer pour une ligne optionnelle. Vide ou blanc, il ne consomme aucune
  rangée. Au pire (logo de 12 lignes + titre sur 2 lignes), l'accueil occupe 22
  rangées sur 24. Les deux routes de l'admin qui enregistrent le formulaire
  (`/save-prompt` ET `/apply-preset`) doivent traiter tout nouveau champ :
  n'en traiter qu'une perdrait la saisie en silence à l'activation.
- **Cadrage des fichiers de connaissance** : `KNOWLEDGE_HEADER` /
  `with_knowledge()` (`minitel_gpt.py`), utilisés par le terminal ET par le
  test de l'admin. La formulation d'origine, « utilise ces informations en
  priorité pour répondre », faisait répondre le modèle **à travers** ces
  documents quelle que soit la question : avec une fiche sur aqoba, « qui est
  le président ? » devenait le président d'aqoba. Le cadrage actuel dit qu'ils
  font autorité sur **leur** sujet, et qu'il faut les ignorer complètement pour
  toute autre question. Si un document déborde encore, c'est son contenu qu'il
  faut resserrer, pas cette phrase.
- **La date du jour, toujours injectée** : un LLM ignore la date et en invente
  une si on ne la lui donne pas (un Guide de Paris répondait « nous sommes le
  19 mai 2024 », un autre inventait le jour de la semaine). `date_note()`
  (`minitel_gpt.py`) ajoute donc systématiquement au prompt « Nous sommes
  aujourd'hui le mardi 1er septembre 2026 » : jour de la semaine, jour et mois
  **réels**, année réelle - sauf si le preset a un `fixed_year`, qui remplace
  la seule année (« vendredi 1er septembre 1989 », le jour de la semaine étant
  recalculé pour l'année effective). Elle prend le preset en argument et est appelée par
  `load_preset()`, donc le terminal et le test de l'admin obtiennent la même
  chose - ce n'était pas le cas de l'ancien `with_fixed_date()` de `server.py`,
  qui relisait la personnalité **active** et n'était pas appelé par l'admin :
  une question sur la date n'y donnait pas la même réponse que sur le Minitel.
  Le conteneur doit avoir son fuseau (`TZ=Europe/Paris` + `tzdata`), sinon il
  tourne en UTC et la date est fausse entre minuit et 2 h. Le champ, **dès
  qu'il est présent**, fait autorité, y compris à `null` pour dire « pas de
  date figée » : sinon `FALLBACK_FIXED_YEARS` (les identifiants historiques
  `annees80` et `annees80bis`) rendait ces personnalités impossibles à libérer
  de 1989, l'admin n'exposant pas ce champ.
- **`%MODEL` dans les messages d'écran** : les trois messages d'un preset
  (`title_msg`, `question_msg`, `loading_msg`) passent par `expand_vars()`
  (`minitel_gpt.py`), qui remplace `%model` (insensible à la casse) par le
  fournisseur actif en majuscules : MISTRAL, CLAUDE ou GEMINI. La valeur vient
  de `llm_settings()`, donc elle suit un changement de fournisseur dans l'admin
  sans redéploiement. Attention aux 40 colonnes : c'est la chaîne **développée**
  qui est tronquée à l'affichage, pas celle saisie dans l'admin. Volontairement
  une seule variable, pas un langage de gabarit à faire vivre.
- **Sécurité `/ws`** : par défaut, aucune authentification — n'importe qui
  connaissant l'URL publique peut discuter et consommer la clé API. Si
  `WS_TOKEN` est configuré côté serveur, `/ws`, `/ws-echo`, `/ws-gemini` et les
  deux routes de données de la dictée (`/dictee/status`, `/dictee/inject`, via le
  même `ws_token_valid()`) exigent `?token=...` en query string (connexion
  WebSocket refusée en silence, 403 sur les routes HTTP). Seule exception
  volontaire : la PAGE `/dictee` est servie sans jeton, sinon celui-ci resterait
  dans l'URL du téléphone ; elle ne contient aucun secret et ne sert à rien sans
  jeton, qu'elle garde dans son `localStorage`.
  L'ESP32 doit inclure le même token dans `WS_PATH` (voir le `.ino`).
- **Dictée téléphone → Minitel** : le téléphone ne se connecte SURTOUT PAS à
  `/ws` (chaque connexion y lance sa propre `run_session`, il ouvrirait une 2e
  conversation au lieu d'écrire dans celle du Minitel). Il fait du HTTP sur
  `/dictee/inject`, qui dépose les octets dans une file portée par le `WSTerm`
  de la session ; `read_byte()` la vide avant d'interroger la WebSocket, donc
  `read_question` reçoit de vraies frappes. Trois pièges :
  1. **Écho.** `read_question` ne ré-échoie pas les frappes, c'est le Minitel
     qui affiche en local ce qu'on tape sur son clavier. Un caractère dicté
     n'ayant jamais été tapé, `_pop_injected()` doit l'écrire lui-même - et
     uniquement l'imprimable, sinon les frappes physiques doubleraient (`BS`
     est déjà rendu par `read_question`, `CR` vaut ENVOI).
  2. **Fenêtre d'écoulement.** La file n'est consommée que pendant
     `read_question` (`with t.injection_allowed():` dans `run_session`) :
     `show_response` ignore les caractères, les afficher écraserait la page.
     Ce qui est dicté pendant la lecture d'une réponse attend son tour.
  3. **Alignement des effacements.** La page envoie un diff (`back` en `BS` +
     suffixe) parce que la dictée iOS réécrit ses phrases en cours de route.
     Son `toAscii()` recopie donc `_ASCII_REPL` à l'identique : si les deux
     translittérations divergent d'un caractère, `back` efface à côté.
- **Prompt système en deux couches** : un preset peut avoir
  `"prompt_file": "nom.txt"` (fichier dans `config/prompts/`) au lieu d'un
  `"prompt"` échappé sur une seule ligne. `resolve_prompt()`
  (`minitel_gpt.py`) tranche **à chaque lecture** : le champ `prompt` s'il est
  renseigné (personnalisation écrite par l'admin web), sinon le contenu du
  `.txt` (défaut du dépôt). Le défaut reste donc vivant - modifier le `.txt` et
  redéployer suffit tant que l'admin n'a rien saisi, **à condition** que
  l'entrypoint rafraîchisse ce `.txt` dans le volume : ce n'était pas le cas
  jusqu'au correctif décrit deux points plus bas - et `ensure_prompts()` ne
  copie plus le texte dans `prompts.json`, pour qu'une installation neuve se
  comporte comme une installation existante. Attention au piège qui a motivé
  ce changement : `p.get("prompt", FALLBACK_PROMPT)` ne protégeait rien, la clé
  existant avec la valeur `""` dans `prompts.default.json`, le LLM recevait un
  prompt vide sans le moindre avertissement. L'admin, lui, n'écrit le champ que
  s'il est non vide : enregistrer la personnalité sans toucher au prompt ne
  fige donc pas le défaut. Pour revenir au défaut une fois personnalisé, vider
  le champ ne suffit pas (une valeur vide est ignorée, justement pour ne pas
  effacer une personnalisation par accident) : il faut cocher « Revenir au
  prompt par défaut du dépôt » dans l'éditeur, qui écrit `""` dans `prompt`.
  Sans cette case, la seule issue était d'éditer `prompts.json` dans le volume,
  et un prompt oublié là faisait dire n'importe quoi au terminal - c'est
  comme ça qu'un « Assistant général » s'est retrouvé figé au 31 décembre 1989,
  alors que ni son `.txt` ni `fixed_year` ne le prévoyaient.

## Variables d'environnement (serveur)

`LLM_PROVIDER` (`mistral`, `claude` ou `gemini`), `MISTRAL_KEY`, `MISTRAL_MODEL`,
`ANTHROPIC_KEY`, `CLAUDE_MODEL`, `GEMINI_KEY`, `GEMINI_MODEL`,
`ADMIN_PASSWORD`, `FLASK_SECRET`, `ADMIN_PUBLIC_URL`, `WS_TOKEN`.

Tout ce qui concerne le LLM (fournisseur, clés, modèles) peut aussi venir de
`config/llm.json`, écrit par l'admin web, qui a la priorité sur ces variables.

## Statut actuel

- [x] Refactor transport + `server.py` validé (import + session simulée + stack
      réel gunicorn/flask-sock testés : accueil, pagination, touches OK).
- [x] Fork créé : https://github.com/omarquet/minitel-gpt, déployé
      (https://minitel.playground.aqoba.fr).
- [x] Support Raspberry Pi entièrement retiré (install.sh, unités systemd,
      sudoers, port série, WiFi captif) ; README.md réécrit pour VPS/ESP32.
- [x] Gemini consolidé dans `minitel_gpt.py` (plus dupliqué dans `server.py`).
- [x] Montage matériel ESP32-C3 + TXS0108E câblé et vérifié.
- [x] Firmware prêt pour la prod : identifiants sortis du fichier suivi
      (`firmware/secrets.h`, ignoré ; modèle `secrets.h.example`), `WS_PATH`
      avec token URL-encodé, reconnexion WiFi/WS durcie, LED de statut.
- [x] Dictée vocale depuis l'iPhone (`/dictee`) : injection dans la session en
      cours, écho serveur, effet machine à écrire. Validée sans matériel
      (fausse WebSocket + vraie `read_question`, écran Videotex simulé, dictée
      iOS rejouée) ; reste à essayer sur le vrai Minitel.
- [ ] Flash du firmware sur la carte et test bout en bout depuis le Minitel.
- [ ] `WS_TOKEN` de production trop court (5 caractères) : à remplacer par une
      valeur aléatoire longue côté variables d'environnement du serveur.
      D'autant plus prioritaire depuis la dictée : le jeton se retrouve dans
      l'URL, donc dans l'historique Safari du téléphone.

## Prochaines pistes possibles

- Exposer `fixed_year` dans l'éditeur de personnalité (aujourd'hui, seul le
  JSON permet de le régler ou de le neutraliser).

- Reconnexion / gestion de plusieurs Minitels simultanés côté serveur.
- Durcir le wss côté ESP32 (empreinte du certificat).
- Support Gemini dans le formulaire de l'admin web (actuellement env var only).
- Graphismes semi-graphiques Videotex (mode mosaïque `SO`/`SI`, déjà défini
  dans `minitel_gpt.py` mais jamais utilisé) : logo au démarrage ou petits
  dessins, en blocs 2x3 colorés par caractère (pas de vraie image bitmap
  possible sur Minitel standard).
