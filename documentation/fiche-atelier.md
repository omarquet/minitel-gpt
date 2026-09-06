---
title: "Minitel 2 Alcatel vers ESP32-C3"
subtitle: "Fiche d'atelier : câblage, tests au multimètre, mise en service. Liaison DIN 5 broches, 1200 bauds 7E1."
---

> **Projet minitel-gpt** (fork omarquet) · révision septembre 2026 · fiche à conserver près de l'établi.
> Source maintenue : `documentation/fiche-atelier.md`. Régénérer le PDF avec `documentation/build-fiche.sh`.

# 1. Vue d'ensemble

L'ESP32 est un **pont transparent** : il relaie les octets bruts entre l'UART du Minitel et le
serveur, sans rien interpréter. Toute la logique Vidéotex vit dans le serveur, un conteneur Docker
sur VPS.

```
Minitel --DIN5 1200 7E1--> ESP32-C3 (UART1) --WiFi wss://--> reverse proxy --> minitel-gpt
```

::: note-new
**Correction par rapport aux versions précédentes de cette fiche.** Le montage est passé sur un
**ESP32-C3**, qui n'a que deux UART : UART0 sert à la console USB, on utilise donc **UART1**.
Surtout, **les GPIO16/17 des anciennes versions sont inutilisables** : sur le C3, les GPIO11 à
GPIO17 sont réservés à la mémoire flash. Le brochage est désormais `GPIO4` en RX et `GPIO5` en TX,
deux broches libres et sans contrainte de démarrage.
:::

| Élément | Détail |
|---|---|
| Minitel 2 Alcatel | Prise péri-informatique DIN 5 broches, à l'arrière |
| ESP32-C3 | UART1 : `GPIO4` RX, `GPIO5` TX. LED de statut sur `GPIO8`, bouton BOOT sur `GPIO9` |
| Adaptation de niveau | Une résistance de 10 k (montages 1 et 2) ou un TXS0108E (montage 3) |
| Alimentation | USB (montages 1 et 3), ou MP1584EN depuis le Minitel (montage 2, §4) |
| Multimètre | Indispensable : tous les contrôles du §5 en dépendent |

::: danger
**Pourquoi une adaptation de niveau est indispensable.** Le port du Minitel travaille en logique
**5 V**. Les GPIO de l'ESP32-C3 sont en **3,3 V** et **ne tolèrent pas le 5 V** : la ligne TX du
Minitel reliée directement à une entrée détruit la broche. Le sens inverse, lui, ne demande rien.
:::

# 2. Brochage de la prise DIN

```
        vue cote BROCHES de la fiche MALE du cable
                (celle que vous tenez en main)

                       ( 2 )              br. 1  NOIR    RX Minitel   <- GPIO5
                                          br. 2  CUIVRE  masse, 0 V
             ( 5 )             ( 4 )      br. 3  ROUGE   TX Minitel   -> GPIO4, 5 V
                                          br. 4  JAUNE   handshake, 5 V MESURES
          ( 3 )                   ( 1 )   br. 5  BLANC   12 V MESURES, danger

                  \___________/
                   detrompeur

     ordre de gauche a droite, cote broches :  3 - 5 - 2 - 4 - 1
```

C'est la vue utile : celle de la fiche qu'on a dans la main au moment de souder. Le détrompeur -
la languette métallique du blindage - se place **en bas**, et les broches se lisent alors 3 - 5 - 2 -
4 - 1 de gauche à droite. La prise du Minitel, vue de face, en est l'image miroir (1 - 4 - 2 - 5 - 3),
mais on ne la voit jamais : elle est à l'arrière de l'appareil.

## Couleurs des fils

Les câbles DIN 5 broches vendus tout faits suivent presque tous la même convention :

| Fil | Broche | Rôle |
|---|---|---|
| Noir | 1 | RX Minitel, reçoit du `GPIO5` |
| Cuivre nu ou tresse | 2 | Masse |
| Rouge | 3 | TX Minitel, 5 V, part vers `GPIO4` |
| Jaune | 4 | Handshake (« PT »), **5 V mesurés** - à isoler, jamais à la masse |
| Blanc | 5 | **12 V, à isoler** sauf montage 2 |

::: danger
**Les deux fils à isoler, et pourquoi on ne les met pas à la masse.** Le blanc (broche 5) porte
12 V, le jaune (broche 4) porte **5 V mesurés** : ce sont deux sorties, pas deux fils morts. Mettre
une broche inutilisée à la masse est le bon réflexe pour une entrée d'un circuit qu'on maîtrise ;
ici, ce serait faire débiter l'appareil dans un court-circuit. Et rien ne lit ces fils du côté
ESP32, donc rien ne « flotte » dans un sens gênant. **Couper court, gainer, rabattre le long du
câble** : le seul risque réel est qu'une extrémité nue touche une piste - le fil blanc et ses 12 V
étant le voisin à craindre.
:::

::: danger
**Cette convention n'est pas une norme.** Elle est très répandue, elle n'est pas garantie : une
série peut inverser deux couleurs sans prévenir. Or se tromper ici, c'est amener les 12 V du fil
blanc sur un GPIO. Les couleurs servent donc à **s'orienter**, pas à conclure. Deux vérifications
suffisent, et elles prennent une minute : la **continuité** entre le fil cuivre et le blindage de
la fiche (§5, test 1), puis le **voltmètre sur le fil blanc**, Minitel allumé, qui doit afficher
12 V (§5, test 3). Si ces deux-là tombent juste, les trois autres suivent.
:::

Les numéros sont parfois moulés en minuscule dans le plastique de la fiche, ce qui permet de
confirmer sans démonter.

::: danger
**Broche 5 : 12 V mesurés sur ce Minitel 2 Alcatel.** Ce n'est plus une inconnue, c'est un résultat
de mesure. **Ne jamais relier cette broche à l'ESP32**, ni sur `3V3`, ni sur `5V`. Sur les petites
cartes C3, la broche `5V` est câblée au VBUS de l'USB et le régulateur embarqué plafonne vers 6 V
d'entrée : la carte est détruite instantanément. Pour s'en servir malgré tout, il faut un
abaisseur - c'est l'objet du §4.
:::

# 3. Trois montages

Chacun est complet : liaison de données **et** alimentation. Prendre le 1 pour démarrer, le 2 pour
un objet autonome, le 3 si l'on tient au décaleur de niveau.

| | 1 · le plus simple | 2 · autonome | 3 · TXS0108E |
|---|---|---|---|
| Adaptation de niveau | 1 résistance | 1 résistance | 1 circuit intégré |
| Alimentation | USB | Minitel, broche 5 | USB |
| Fils DIN utilisés | 1, 2, 3 | 1, 2, 3, 5 | 1, 2, 3 |
| Se déplace sans ordinateur | non | **oui** | non |
| Ordre de branchement | indifférent | masse d'abord | **USB avant le DIN** |
| État | mesuré sur ce Minitel | mesuré, buck à régler | montage historique validé |

Les trois partagent la même liaison descendante : `GPIO5` vers la broche 1, **en direct**. Le 3,3 V
est accepté sans souci par la plupart des entrées TTL, dont le seuil est vers 2,0 à 2,4 V, mais cela
se vérifie (§5, test 6). **Si ça ne passe pas**, un **74HCT125** alimenté en 5 V fait ce sens
proprement, ses entrées HCT considérant 3,3 V comme un niveau haut franc.

## Montage 1 · pull-up simple, alimenté par l'USB

Une résistance, trois fils. C'est celui par lequel commencer.

```
   fiche DIN                                      ESP32-C3
   (cote broches)                            (alimente par l'USB)


   br.3  ROUGE   TX Minitel ----+---------------->  GPIO4  (RX)
                                |
                             [ 10k ]
                                |
                               3V3  (rail de l'ESP32)


   br.1  NOIR    RX Minitel <----------------------  GPIO5  (TX)
                              liaison directe, 3,3 V

   br.2  CUIVRE  masse      ----------------------->  GND


   br.4  JAUNE (5 V)  et  br.5  BLANC (12 V) : coupes court, gaines, non connectes
```

La norme STUM1B documente la sortie TX du Minitel comme un **collecteur ouvert** : elle ne pousse
jamais activement vers le haut, elle tire seulement vers le bas (bit à 0) et laisse la ligne flotter
au repos (bit à 1). Ce genre de sortie a besoin d'un pull-up externe pour définir son niveau haut -
rien de plus. Une résistance unique de **10 k vers le rail 3,3 V** de l'ESP32 fait ce travail, sans
qu'aucune tension de 5 V n'ait besoin d'être divisée : il n'y en a pas à diviser, puisque rien ne
pousse la ligne à 5 V.

**Mesuré sur ce Minitel 2 Alcatel** : point de jonction à environ 3,3 V au repos, résistance de
10 k, `GPIO4` relié en direct - aucun second composant vers la masse. Test 6 (transmission montante)
passé sans corruption.

Choix de la valeur : 10 k, ni trop de courant gaspillé, ni ligne trop molle - et c'est ce qu'on a
déjà sous la main.

## Montage 2 · pull-up simple, alimenté par le Minitel

Le montage 1, dont l'USB est remplacé par un abaisseur MP1584EN branché sur les 12 V de la broche 5.
Plus d'ordinateur : l'ensemble démarre en branchant le seul Minitel. **Le réglage du buck et ses
pièges font l'objet du §4 - le lire avant de câbler quoi que ce soit.**

```
   fiche DIN                   MP1584EN                      ESP32-C3
   (cote broches)         4 pastilles, 2 par bord         (USB DEBRANCHE)

                         +--------------------------+
   br.5  BLANC  12 V --> | IN+                 OUT+ | ----------->  5V  --+
                         |                          |                     |
                         |     (o) VR : reglage     |               [ 100-220 uF ]
                         |                          |                     |  optionnel
   br.2  CUIVRE  ----+-> | IN-                 OUT- | ----------->  GND --+
                     |   +--------------------------+                     |
                     +---------- masse commune ---------------------------+
                     |
                     |                                        GPIO4  (RX)
                     |                                           ^
   br.3  ROUGE  -----+-------------------------------------------+
                                                                 |
                                                              [ 10k ]
                                                                 |
                                                                3V3  (rail de l'ESP32,
                                                                      fourni par le buck)


   br.1  NOIR  <-------------------------------------------  GPIO5  (TX)
                         liaison directe, 3,3 V
```

La masse est le fil qui relie tout - broche 2, entrée et sortie du buck, et `GND` de l'ESP32 - et
c'est le premier à brancher, le dernier à débrancher. La résistance de pull-up tire toujours sur le
rail 3,3 V de l'ESP32, alimenté ici par le buck plutôt que par l'USB.

**Ne jamais laisser l'USB branché en même temps** : la broche `5V` de l'ESP32-C3 est le VBUS de
l'USB, deux sources se retrouveraient en conflit sur le même rail.

## Montage 3 · TXS0108E, alimenté par l'USB

Le montage historique, validé sur ce Minitel. Un décaleur de niveau bidirectionnel remplace la
résistance : plus de composants, plus de règles à respecter, mais c'est celui qui a servi le plus
longtemps.

```
   ESP32-C3                  TXS0108E                    prise DIN
   (USB)                (a cheval sur la rainure)

   3V3  ------------------>  VA , OE
   5V   ------------------>  VB   <........................ retour parasite
   GND  ------------------>  GND  ----------------------->  br. 2  masse   (cuivre)
   GPIO5 (TX) ----------->  A1 <-> B1  ------------------>  br. 1  RX      (noir)
   GPIO4 (RX) <-----------  A2 <-> B2  <------------------  br. 3  TX      (rouge)

            cote A = 3,3 V (ESP32)   |   cote B = 5 V (Minitel)
```

| Broche du TXS0108E | Reliée à |
|---|---|
| `VA` | 3,3 V de l'ESP32 |
| `VB` | 5 V de l'ESP32 (rail +) |
| `GND` | Masse commune (rail −) |
| `OE` | VA, soit 3,3 V. Sinon les sorties restent en haute impédance |
| `A1 ↔ B1` | `GPIO5` (TX ESP32) ↔ broche 1 (RX Minitel) |
| `A2 ↔ B2` | `GPIO4` (RX ESP32) ↔ broche 3 (TX Minitel) |

::: note
**Trois règles, et un ordre à respecter.** VA ne doit **jamais** dépasser VB. **Aucune résistance**
sur les lignes de données : elle perturbe la détection automatique de sens. Le circuit se pose **à
cheval sur la rainure** centrale de la plaque, sinon ses deux rangées sont court-circuitées.

**Le retour parasite du schéma** : VB est pris sur la broche `5V` de l'ESP32-C3, qui *est* le VBUS
de l'USB. USB débranché, le 5 V permanent de la ligne au repos entre par la diode de protection du
TXS0108E, atteint VB, donc le rail de la carte : la LED d'alimentation s'allume faiblement et tout
est dans un état indéterminé. D'où la règle : **brancher l'USB d'abord, le DIN ensuite ; débrancher
dans l'ordre inverse.** Une diode Schottky (BAT54, 1N5817) entre la broche `5V` et VB bloque ce
retour et rend l'ordre indifférent, au prix de 0,25 V sur VB.
:::

# 4. Le MP1584EN en détail (montage 2)

La broche 5 délivre **12 V** : il faut donc un abaisseur, **réglé avant tout branchement**. Le
schéma du montage complet est au §3, montage 2 ; ce qui suit en donne le mode d'emploi.

| Pastille du module | Nom alternatif sur certaines séries | Reliée à |
|---|---|---|
| `IN+` | `VIN` | DIN broche 5, 12 V |
| `IN-` | `GND` | DIN broche 2, masse |
| `OUT+` | `VOUT`, `+` | Broche `5V` de l'ESP32 |
| `OUT-` | `GND`, `-` | Masse commune |
| `VR` | potentiomètre multitours bleu | Ne se touche qu'à l'étape 1 ci-dessous, module débranché de l'ESP32 |

Les quatre pastilles sont réparties **deux par bord** : entrée d'un côté, sortie de l'autre. Le
potentiomètre est multitours : il faut plusieurs tours complets pour parcourir la plage, la tension
ne bouge donc pas au premier quart de tour - continuer en surveillant le voltmètre plutôt que de
forcer.

## Procédure, dans cet ordre

1. **Régler le buck, alimenté par le Minitel lui-même, sortie en l'air.** Relier **uniquement**
   `IN+` à la broche 5 et `IN-` à la broche 2 - **rien sur `OUT+`**. Minitel allumé, voltmètre sur
   `OUT+` / `OUT-`, tourner le potentiomètre jusqu'à lire **5,0 V**. Régler sous la tension d'entrée
   réelle vaut mieux qu'avec une pile de laboratoire, et ne demande aucun matériel de plus.
2. **Éteindre ou débrancher le DIN**, puis relier `OUT+` à la broche `5V` de l'ESP32 et `OUT-` à la
   masse. Rebrancher ensuite.
3. **Vérifier la broche 5 en charge** (§5, test 3) : une tension à vide ne prouve rien, c'est le
   débit qui compte. Revérifier aussi la sortie du buck **ESP32 connecté et WiFi actif** : si elle
   tombe sous 4,7 V, la source est trop faible pour ce montage.
4. **Relier la masse en premier**, puis le 5 V. Débrancher dans l'ordre inverse.
5. **Ne jamais laisser l'USB branché** en même temps : la broche `5V` de l'ESP32-C3 est le VBUS de
   l'USB, deux sources se retrouveraient en conflit sur le même rail. Pour garder les deux
   possibles, une Schottky en série sur la sortie du buck.
6. **Essayer sans condensateur d'appoint**, puis lire le moniteur série (ci-dessous).

## Le condensateur d'appoint : seulement si le montage le réclame

Le module MP1584EN a déjà son condensateur de sortie, la carte ESP32 les siens, et le buck débite
3 A là où l'ESP32-C3 demande des pointes de 300 mA : **la capacité de courant n'est pas le
problème**. Ce qui l'est, c'est la brutalité de l'appel au passage en émission WiFi - la boucle de
régulation met quelques dizaines de µs à réagir, et la résistance des fils de plaque d'essai
transforme le pic en chute de tension **au pied de l'ESP32**, pas à la sortie du buck où l'on
mesure.

Le firmware tranche tout seul : il affiche la cause du dernier démarrage sur le moniteur série.

| Ligne au démarrage | Verdict |
|---|---|
| `mise sous tension`, `bouton RESET` | L'alimentation tient, aucun condensateur à ajouter |
| `BROWNOUT (alimentation insuffisante)` | Il en faut un |
| Redémarrages au moment où le WiFi se connecte | Il en faut un |

Le cas échéant : **100 à 220 µF électrolytique, plus 10 µF céramique**, au ras des broches `5V` et
`GND` de l'ESP32 - pas à la sortie du buck. Le céramique encaisse le front rapide, que
l'électrolytique, plus lent, ne voit même pas. **1000 µF n'est pas « plus sûr »** : à l'enfichage,
un tel condensateur appelle un courant de charge qui peut mettre le buck en protection, d'autant
plus que la broche 5 est une source faible. Sur plaque d'essai, raccourcir les fils gagne souvent
plus qu'ajouter de la capacité.

::: danger
**Tant que le réglage n'est pas fait, rien ne se branche en aval.** Certains modules sortent d'usine
réglés au maximum : une sortie à 12 V sur la broche `5V` de l'ESP32-C3 détruit la carte
instantanément. Tourner le potentiomètre dans le vide ne risque rien, alors qu'aucune fausse
manœuvre n'est rattrapable une fois l'ESP32 relié. Si vous intercalez une diode Schottky en sortie
(pour pouvoir garder l'USB), réglez à **5,3 V** : l'ESP32 verra 5 V après la chute de la diode.
:::

::: danger
**Le courant disponible est faible et mal documenté.** La broche 5 n'est pas une prise de courant :
elle alimentait des périphériques peu gourmands. Un ESP32 en émission WiFi demande des pointes de
300 mA. Mesurer en charge avant de considérer le montage fiable, et garder l'USB comme repli pour
les démonstrations qui comptent.
:::

# 5. Tests au multimètre

Multimètre en tension continue (`V⎓` / `DCV`), calibre 20 V si le réglage est manuel. Pointe noire
(COM) toujours sur la masse, pointe rouge sur le point à tester.

## Test 1 · Identifier la masse — Minitel éteint

En mode continuité. Une pointe sur la broche 2, l'autre sur le blindage métallique du connecteur.
Continuité attendue (bip, ou ≈ 0 Ω). **Ce test valide tout le repérage du connecteur : à faire en
premier**, avant toute mise sous tension.

## Test 2 · Le pull-up, hors tension

En ohmmètre, ESP32 éteint, résistance câblée mais fil DIN 3 pas encore relié :

| Entre | Attendu |
|---|---|
| Point M et `3V3` de l'ESP32 | 10 kΩ |

## Test 3 · La broche 5 débite-t-elle ? — Minitel allumé

Noire sur broche 2, rouge sur broche 5.

| Lecture | Interprétation | Action |
|---|---|---|
| 12 V stables | Sortie d'alimentation présente *(cas mesuré ici)* | Utilisable via un abaisseur seulement (§4) |
| 0 V | Broche de signal, pas d'alimentation | Ne rien y brancher |
| Valeur fluctuante | Signal logique, pas une source | Ne rien y brancher |

**Test de charge complémentaire :** brancher 1 kΩ entre broche 5 et masse tout en mesurant. Si la
tension s'effondre, la broche ne débite pas assez. Si elle tient, c'est une vraie source.

**La broche 4 (fil jaune) affiche elle aussi 5 V** sur ce Minitel. Le même test de charge dirait
s'il s'agit d'une source ou d'une simple résistance de tirage, mais la question est théorique : le
montage n'en a pas besoin - les montages 1 et 2 ne demandent aucun 5 V, et le 3 prend le sien sur
le rail de l'ESP32. On l'isole, on n'y touche pas.

## Test 4 · La ligne de données — Minitel allumé, en mode péri-informatique

| Point | Attendu |
|---|---|
| Broche 3 (TX Minitel), fil seul, rien branché dessus | flotte, lecture instable |
| Jonction du pull-up, montages 1 et 2 | ≈ 3,3 V |

Une ligne série au repos est au niveau haut : avec le pull-up, la lecture doit être stable autour de
3,3 V, et bouger légèrement à la frappe. Le multimètre ne fausse rien : avec ses 10 MΩ d'entrée face
aux 10 kΩ du pull-up, l'erreur est négligeable.

| Si M lit | Cause |
|---|---|
| ≈ 3,3 V, stable | Conforme |
| 0 V fixe | Résistance absente ou non reliée au `3V3`, ou TX resté bas (mode péri-informatique pas activé : `Fnct`+`T` puis `A`) |
| Tension instable ou intermédiaire | Continuité douteuse sur le fil DIN 3, ou broche 3 mal identifiée |

## Test 5 · Le retour de courant

**Débrancher l'USB, laisser le DIN branché**, et mesurer la broche `3V3` de l'ESP32 contre la masse.

| Montage | Lecture | LED d'alimentation |
|---|---|---|
| Montage 3, sans diode | plusieurs volts | faiblement allumée |
| Montages 1 et 2 | non mesuré | non mesuré |

## Test 6 · Fonctionnel, sans multimètre

Mettre `DEBUG_UART` à `1` dans le `.ino`, flasher, et regarder :

- **`TEST ESP32 OK` s'affiche sur le Minitel au démarrage** : le sens descendant est bon - et cela
  valide que le Minitel accepte les 3,3 V en direct.
- **`[RX] 0x..` défile sur le moniteur série à la frappe** : le sens montant est bon, donc le pull-up
  aussi.

Remettre `DEBUG_UART` à `0` ensuite.

# 6. Mise en service du Minitel

Le réglage qui compte s'appelle le **mode péri-informatique**. Par défaut, le Minitel route son
clavier et son écran vers son **modem interne** - il est fait pour téléphoner. Passer en mode
péri-informatique, c'est réaiguiller ces deux flux vers la **prise DIN** : le clavier sort par la
broche 3, l'écran est alimenté par la broche 1. Sans cet aiguillage, le câblage est parfait et il ne
se passe rien.

**À refaire à chaque allumage** : le Minitel ne mémorise aucun de ces réglages.

## Commandes clavier

| Combinaison | Effet |
|---|---|
| `Fnct` + `T` puis `A` | **Aiguillage vers la prise** : passage en mode péri-informatique |
| `Fnct` + `T` puis `E` | Coupe l'écho local du clavier |
| `Fnct` + `Sommaire` | Si besoin : passe du mode répertoire au mode terminal |
| `Fnct` + `P` puis `1` | Prise à **1200 bauds** - la vitesse attendue par le firmware |
| `Fnct` + `P` puis `3` | Prise à 300 bauds |
| `Fnct` + `P` puis `4` | Prise à 4800 bauds, selon le modèle |
| `Fnct` + `P` puis `9` | Prise à 9600 bauds, Minitel 2 seulement |

Le chiffre de `Fnct` + `P` est le **premier chiffre de la vitesse** : 3 pour 300, 1 pour 1200, 4
pour 4800, 9 pour 9600. C'est le seul moyen mnémotechnique de s'en souvenir devant la machine.

::: note
**La vitesse doit rester à 1200 bauds, 7E1.** C'est la valeur de sortie d'usine de la prise, et
celle du firmware (`SERIAL_7E1`, 1200). Ces combinaisons ne servent donc qu'à **revenir** en arrière
si la vitesse a été changée par mégarde - le symptôme est sans ambiguïté : des caractères corrompus
ou aléatoires alors que le câblage est bon. Monter à 4800 demanderait de changer aussi le firmware,
sans bénéfice : le Minitel affiche de toute façon plus lentement qu'il ne reçoit.
:::

## La version logicielle du « Fnct+T puis A »

Le firmware envoie lui-même l'aiguillage, par une séquence **PRO3** sur la liaison série (`ESC 3B`,
`AIGUILLAGE_ON`, récepteur, émetteur), répétée pendant les 5 premières secondes après le démarrage -
le Minitel peut être encore en train de s'initialiser au moment du premier envoi, et la séquence
serait perdue en silence.

::: note
**Non vérifié sur ce Minitel 2 Alcatel.** La couche Protocole fait partie de la norme Teletel
commune à toute la gamme, et la séquence est documentée pour le Minitel 1B, mais personne ne l'a
encore confirmée sur ce modèle. Si le clavier ne remonte rien sans avoir tapé `Fnct` + `T` puis `A`
à la main, c'est que l'aiguillage automatique n'a pas eu l'effet attendu : la manipulation manuelle
reste le repli, et elle ne coûte rien.
:::

# 7. Firmware et configuration

Tout ce qui est propre à une installation vit dans `firmware/secrets.h`, jamais dans le `.ino` - qui
refuse de compiler si une valeur manque, avec un message qui dit laquelle.

| Définition | Rôle |
|---|---|
| `WIFI_SSID` / `WIFI_PASSWORD` | Réseau principal |
| `WIFI_SSID2` / `3` | Facultatifs, essayés dans l'ordre. Le partage de connexion du téléphone a sa place ici |
| `USE_LOCAL` | `0` = production en `wss://`, `1` = serveur local en `ws://` |
| `WS_HOST_PROD` / `WS_PORT_PROD` | Serveur public |
| `WS_HOST_LOCAL` / `WS_PORT_LOCAL` | Machine de développement, si `USE_LOCAL 1` |
| `WS_TOKEN_ENC` | Jeton du serveur, **URL-encodé**. Un jeton absent ou faux fait fermer la connexion en silence |

## Configurer le WiFi sans ordinateur

Au démarrage, dans l'ordre : le réseau mémorisé, puis la liste de `secrets.h`. Si rien ne répond,
**l'ESP32 prend la parole sur le Minitel** et propose les réseaux du scan, numérotés : on choisit au
chiffre - le SSID ne se tape jamais - puis on saisit le mot de passe, affiché en clair. Le réseau
retenu est mémorisé pour les fois suivantes.

| Geste | Effet |
|---|---|
| Bouton BOOT, **appui bref** | Ouvre l'écran de configuration immédiatement, sans attendre les essais |
| Bouton BOOT, **maintenu 5 s** | Oublie le réseau mémorisé et redémarre. LED fixe pendant l'appui, 3 flashs à l'effacement |
| `O` dans le menu | Même effet, depuis l'écran de configuration |

::: note
**BOOT se lit en fonctionnement, jamais au démarrage.** `GPIO9` est la broche de strapping qui
choisit le mode de démarrage : maintenue basse **au reset**, la puce entre en mode téléversement et
n'exécute pas le firmware. C'est pourquoi le geste est un appui pendant que la carte tourne, et non
au branchement.
:::

## La LED de statut, sur GPIO8

Logique inversée : `LOW` = allumée.

| Comportement | Signification |
|---|---|
| Flash bref toutes les 2 s | Tout va bien |
| Clignotement lent | WebSocket coupée |
| Clignotement rapide | WiFi perdu |
| Fixe | Appui sur BOOT en cours |
| Figée | `loop()` bloqué |

## Depuis le Minitel, en cours d'usage

| Touche | Effet |
|---|---|
| `Guide` | Liste des personnalités, un chiffre pour changer |
| `Guide` puis `Suite` | Choix du modèle d'IA (A, B, C). Accès non annoncé à l'écran |
| `Sommaire` | Retour à l'accueil |

# 8. Dépannage

| Symptôme | Piste |
|---|---|
| Rien ne s'affiche, rien ne remonte | Montage 3 : OE non relié à VA. Sinon : masse commune absente, ou Minitel pas en mode péri-informatique (`Fnct`+`T` puis `A`) |
| Ça reçoit mais n'émet pas, ou l'inverse | TX et RX inversés : intervertir broches 1 et 3 |
| Chaque caractère s'affiche en double | Écho local actif : `Fnct`+`T` puis `E` |
| Caractères corrompus ou aléatoires | Vitesse ou format (attendu 1200 7E1) ; fils trop longs. Le TXS0108E supporte mal les lignes capacitives, le pull-up simple des montages 1 et 2 est plus prévisible |
| LED d'alimentation faiblement allumée, USB débranché | Retour de courant par la ligne de données. Diode Schottky sur VB, ou passage au montage 1 |
| L'ESP32 redémarre en boucle | Le moniteur série annonce `BROWNOUT` : alimentation insuffisante lors des pics WiFi. 100 à 220 µF plus 10 µF céramique au ras des broches `5V` et `GND` (§4) |
| `[WS] connecte` puis `deconnecte` en boucle | Jeton absent ou faux : le serveur ferme en silence. Vérifier `WS_TOKEN_ENC`, URL-encodé |
| Boucle de scan WiFi sans jamais d'écran | Firmware antérieur à septembre 2026 : le scan lancé pendant une tentative de connexion était refusé. Mettre à jour |

---

*Les valeurs de tension de cette fiche ont été mesurées sur un Minitel 2 Alcatel : les vérifier sur
tout autre modèle.*
