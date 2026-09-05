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
| Adaptation de niveau | Au choix : TXS0108E (variante A) ou deux résistances (variante B) |
| Alimentation | USB pendant la mise au point, ou MP1584EN depuis le Minitel (§4) |
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
                                          br. 4  JAUNE   handshake, non utilise
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
| Jaune | 4 | Handshake, non utilisé - à isoler |
| Blanc | 5 | **12 V, à isoler** sauf montage du §4 |

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

# 3. Câblage : deux variantes

Les deux fonctionnent. La **A** est le montage historique, validé sur ce Minitel. La **B** est plus
simple, plus prévisible, et supprime deux défauts de la A.

## Variante A · TXS0108E

```
   ESP32-C3                  TXS0108E                    prise DIN
   (USB)                (a cheval sur la rainure)

   3V3  ------------------>  VA , OE
   5V   ------------------>  VB   <........................ retour parasite
   GND  ------------------>  GND  ----------------------->  br. 2  masse   (cuivre)
   GPIO5 (TX) ----------->  A1 <-> B1  ------------------>  br. 1  RX      (noir)
   GPIO4 (RX) <-----------  A2 <-> B2  <------------------  br. 3  TX, 5 V (rouge)

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

## Variante B · deux résistances

La liaison montante est **unidirectionnelle** : c'est toujours le Minitel qui pousse le signal vers
l'ESP32. Un pont diviseur suffit donc - et un pont ne sait qu'abaisser une tension, ce qui est
exactement ce qu'on demande ici.

```
   DIN br. 3  (TX Minitel, 5 V, fil ROUGE)
        |
     [ 10k ]
        |
        +-------------------------------> GPIO4  (RX ESP32)      M = 3,33 V
        |
     [ 20k ]
        |
       GND  (DIN br. 2, masse commune, fil CUIVRE)


   GPIO5  (TX ESP32, 3,3 V) --------------> DIN br. 1 (NOIR)   liaison DIRECTE
```

Le point milieu M vaut `5 × 20 / (10 + 20) = 3,33 V` quand la ligne est haute, 0 V quand elle est
basse. Si vous n'avez que des 10 k, **deux en série font le 20 k**. Placer le pont **près de
l'ESP32** : le long fil est ainsi attaqué par la sortie basse impédance du Minitel.

### Choix des valeurs

| R haut | R bas | V sortie | Courant au repos | τ à 100 pF |
|---|---|---|---|---|
| 1 k | 2 k | 3,33 V | 1667 µA | 0,07 µs |
| 4,7 k | 10 k | 3,40 V | 340 µA | 0,32 µs |
| **10 k** | **20 k** | **3,33 V** | **167 µA** | **0,67 µs** |
| 22 k | 47 k | 3,41 V | 72 µA | 1,50 µs |

Trop bas, on gaspille du courant ; trop haut, la ligne devient molle et sensible au bruit.
**10 k / 20 k** est le bon compromis. La vitesse n'est pas un sujet : un bit à 1200 bauds dure
**833 µs** quand la constante de temps du pont est de 0,67 µs, soit un facteur 1250. C'est parce
que le Minitel est lent que cette solution rustique est ici parfaitement propre.

### Ce que la variante B règle

ESP32 éteint, ligne toujours à 5 V, le courant qui peut entrer dans la carte est fixé par la
résistance de 10 k :

| Tension du rail 3,3 V | Courant injecté |
|---|---|
| 0 V | 410 µA |
| 0,5 V | 335 µA |
| 1,0 V | 260 µA |

Quelques centaines de microampères au maximum. La LED d'alimentation de la carte en consomme à elle
seule plusieurs milliampères : **le rail ne peut pas monter**. Avec le TXS0108E, à l'inverse, ce
courant ne traverse qu'une diode interne, sans autre limite que ce que débite le Minitel. La règle
« USB d'abord » disparaît donc avec la variante B.

::: note
**Le seul point à vérifier :** que le Minitel lise bien 3,3 V comme un niveau haut sur la broche 1,
en liaison directe. C'est le cas de la plupart des entrées TTL, dont le seuil est vers 2,0 à 2,4 V,
mais cela se teste (§5, test 6). **Si ça ne passe pas**, ne revenez pas au TXS0108E : un **74HCT125**
alimenté en 5 V fait ce sens proprement, ses entrées HCT considérant 3,3 V comme un niveau haut
franc. Un boîtier, un sens fixe, aucune détection automatique à perturber - et le pont diviseur
reste pour le sens montant.
:::

## Choisir

| | A · TXS0108E | B · résistances |
|---|---|---|
| Composants | 1 circuit intégré, plaque à cheval | 2 résistances |
| Retour de courant, ESP32 éteint | Non limité | ≤ 410 µA |
| Ordre de branchement | USB d'abord, impérativement | Indifférent |
| Sensibilité aux fils longs | Élevée (détection de sens) | Faible |
| Point d'incertitude | Aucun, montage validé | Seuil d'entrée du Minitel à 3,3 V |

# 4. Alimenter l'ESP32 par le Minitel

Objectif : se passer de l'USB, pour que l'ensemble démarre en branchant le seul Minitel. La broche 5
délivre **12 V** : il faut donc un abaisseur, **réglé avant tout branchement**.

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
   br.3  ROUGE 5 V --+---[ 10k ]---+---------------------------+
                     |             |
                     |          [ 20k ]
                     +-------------+


   br.1  NOIR  <-------------------------------------------  GPIO5  (TX)
                         liaison directe, 3,3 V
```

| Pastille du module | Nom alternatif sur certaines séries | Reliée à |
|---|---|---|
| `IN+` | `VIN` | DIN broche 5, 12 V |
| `IN-` | `GND` | DIN broche 2, masse |
| `OUT+` | `VOUT`, `+` | Broche `5V` de l'ESP32 |
| `OUT-` | `GND`, `-` | Masse commune |
| `VR` | potentiomètre multitours bleu | Ne se touche qu'au §4, étape 1, module débranché de l'ESP32 |

Les quatre pastilles sont réparties **deux par bord** : entrée d'un côté, sortie de l'autre. Le
potentiomètre est multitours : il faut plusieurs tours complets pour parcourir la plage, la tension
ne bouge donc pas au premier quart de tour - continuer en surveillant le voltmètre plutôt que de
forcer.

Le montage complet, alimenté par le seul Minitel : plus d'USB, plus de rail 5 V venant d'ailleurs.
La masse est le fil qui relie tout - broche 2, entrée et sortie du buck, base du pont diviseur et
`GND` de l'ESP32 - et c'est le premier à brancher, le dernier à débrancher.

Le schéma reprend la **variante B** du §3. Avec la variante A, le `VB` du TXS0108E se prend sur le
même rail 5 V que l'ESP32, en sortie du buck : dans ce montage autonome, la règle « USB d'abord »
n'a plus d'objet, puisqu'il n'y a plus d'USB.

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

## Test 2 · Le pont diviseur, hors tension

En ohmmètre, pont câblé mais `GPIO4` pas encore relié :

| Entre | Attendu |
|---|---|
| Fil DIN 3 et masse | 30 kΩ |
| Point milieu M et masse | 20 kΩ |

## Test 3 · La broche 5 débite-t-elle ? — Minitel allumé

Noire sur broche 2, rouge sur broche 5.

| Lecture | Interprétation | Action |
|---|---|---|
| 12 V stables | Sortie d'alimentation présente *(cas mesuré ici)* | Utilisable via un abaisseur seulement (§4) |
| 0 V | Broche de signal, pas d'alimentation | Ne rien y brancher |
| Valeur fluctuante | Signal logique, pas une source | Ne rien y brancher |

**Test de charge complémentaire :** brancher 1 kΩ entre broche 5 et masse tout en mesurant. Si la
tension s'effondre, la broche ne débite pas assez. Si elle tient, c'est une vraie source.

## Test 4 · La ligne de données — Minitel allumé, en mode péri-informatique

| Point | Attendu |
|---|---|
| Broche 3 (TX Minitel) | ≈ 5,0 V |
| Point milieu M du pont | 3,33 V |

Une ligne série au repos est au niveau haut : la lecture doit être stable, et bouger légèrement à la
frappe. Le multimètre ne fausse rien : avec ses 10 MΩ d'entrée face aux 6,7 kΩ du pont, l'erreur est
de **0,07 %**. Seul un vieil appareil à aiguille (20 kΩ/V) fausserait la lecture, de 25 %.

| Si M lit | Cause |
|---|---|
| 5,00 V | La 20 k ne touche pas la masse |
| 1,67 V | Les deux résistances sont inversées |
| 0,00 V | La 10 k ne touche pas la broche 3, ou le Minitel est éteint |

## Test 5 · Le retour de courant — le test qui valide la variante B

**Débrancher l'USB, laisser le DIN branché**, et mesurer la broche `3V3` de l'ESP32 contre la masse.

| Montage | Lecture | LED d'alimentation |
|---|---|---|
| Variante A, sans diode | plusieurs volts | faiblement allumée |
| Variante B | quelques dizaines de mV | éteinte |

## Test 6 · Fonctionnel, sans multimètre

Mettre `DEBUG_UART` à `1` dans le `.ino`, flasher, et regarder :

- **`TEST ESP32 OK` s'affiche sur le Minitel au démarrage** : le sens descendant est bon - et en
  variante B, cela valide que le Minitel accepte les 3,3 V.
- **`[RX] 0x..` défile sur le moniteur série à la frappe** : le sens montant est bon, donc le pont
  diviseur aussi.

Remettre `DEBUG_UART` à `0` ensuite.

# 6. Mise en service du Minitel

À refaire **à chaque allumage** : le Minitel ne mémorise pas ces réglages.

| Combinaison | Effet |
|---|---|
| `Fnct` + `T` puis `A` | Passage en mode péri-informatique |
| `Fnct` + `T` puis `E` | Coupe l'écho local du clavier |
| `Fnct` + `Sommaire` | Si besoin : passe du mode répertoire au mode terminal |

::: note
**Ne pas toucher à la vitesse.** La prise démarre à 1200 bauds, 7E1, ce qui correspond au firmware
(`SERIAL_7E1`, 1200). Les combinaisons `Fnct` + `P` ne servent pas ici.
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
| Rien ne s'affiche, rien ne remonte | Variante A : OE non relié à VA. Sinon : masse commune absente, ou Minitel pas en mode péri-informatique (`Fnct`+`T` puis `A`) |
| Ça reçoit mais n'émet pas, ou l'inverse | TX et RX inversés : intervertir broches 1 et 3 |
| Chaque caractère s'affiche en double | Écho local actif : `Fnct`+`T` puis `E` |
| Caractères corrompus ou aléatoires | Vitesse ou format (attendu 1200 7E1) ; fils trop longs. Le TXS0108E supporte mal les lignes capacitives, le pont diviseur est plus prévisible |
| LED d'alimentation faiblement allumée, USB débranché | Retour de courant par la ligne de données. Diode Schottky sur VB, ou passage en variante B |
| L'ESP32 redémarre en boucle | Le moniteur série annonce `BROWNOUT` : alimentation insuffisante lors des pics WiFi. 100 à 220 µF plus 10 µF céramique au ras des broches `5V` et `GND` (§4) |
| `[WS] connecte` puis `deconnecte` en boucle | Jeton absent ou faux : le serveur ferme en silence. Vérifier `WS_TOKEN_ENC`, URL-encodé |
| Boucle de scan WiFi sans jamais d'écran | Firmware antérieur à septembre 2026 : le scan lancé pendant une tentative de connexion était refusé. Mettre à jour |

---

*Les valeurs de tension de cette fiche ont été mesurées sur un Minitel 2 Alcatel : les vérifier sur
tout autre modèle.*
