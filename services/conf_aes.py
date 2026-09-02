#!/usr/bin/env python3
"""
conf_aes.py - tout ce qui concerne Agile en Seine, et rien d'autre.

Module VOLONTAIREMENT isole : c'est un contenu evenementiel, destine a etre
retire une fois la conference passee. Pour le supprimer, il suffit d'effacer
ce fichier, les deux appels a prompt_note() (server.py et admin_ui.py), la
personnalite "agile_en_seine" de prompts.default.json et son prompt .txt.

C'est aussi la seule fenetre sur le web en temps reel de tout le projet : le
programme change jusqu'au dernier moment, on va donc lire la page officielle.
Pas de tool-calling generique, juste ce cas precis, en dur.
"""
import re
import logging
from datetime import datetime
from html import unescape

import requests

log = logging.getLogger("minitel-gpt")

URL = "https://www.agileenseine.com/programme-2026/"
# Mots-clefs : n'importe quelle personnalite qui recoit une question sur Agile
# en Seine obtient le programme (les annees 80 en font une de leurs deux
# exceptions factuelles, cf. config/prompts/annees80.txt).
KEYWORDS = ("agile en seine", "agileenseine", "aes")
# La personnalite dediee, pour qui le programme est injecte a CHAQUE question :
# "c'est quoi les prochaines confs ?" ne contient aucun mot-clef.
PRESET_KEY = "agile_en_seine"
# Plafond du contenu injecte, aligne sur celui des fichiers de connaissance.
# A 4000 caracteres, plus de la moitie du programme etait coupee (42 creneaux
# horaires sur 100) et la troncature tombait en plein titre de session : le
# modele repondait alors a cote sur les conferences de fin de journee, sans
# rien signaler.
MAX_CHARS = 12000
TIMEOUT = 8

_SLOT = re.compile(r"\d{2}:\d{2} - \d{2}:\d{2}")
# Chaque grille embarque ce message d'etat vide, cache en display:none. Recopie
# tel quel, il faisait dire au modele qu'une journee pourtant pleine etait vide.
_EMPTY_NOTICE = re.compile(r"Aucun programme n'est pr[ée]vu[^.]*", re.I)


def is_question(text):
    """La question porte-t-elle sur Agile en Seine ?"""
    t = (text or "").lower()
    return any(k in t for k in KEYWORDS)


def _sessions(html_fragment):
    """HTML d'une grille -> texte, une session par ligne. "" si la journee
    n'a encore aucune session publiee."""
    x = re.sub(r"<script[^>]*>.*?</script>", " ", html_fragment, flags=re.S | re.I)
    x = re.sub(r"<style[^>]*>.*?</style>", " ", x, flags=re.S | re.I)
    x = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x)).strip()
    x = unescape(x)                      # &rsquo; &amp; &#038; dans les titres
    x = _EMPTY_NOTICE.sub("", x)
    # Le fouillis d'avant la premiere session (menu, filtres, intitules) ne dit
    # rien du programme et coute des caracteres.
    first = _SLOT.search(x)
    if not first:
        return ""
    # Une session par ligne : mises bout a bout, le modele collait la salle
    # d'une session a l'horaire de la suivante.
    return _SLOT.sub(lambda m: "\n" + m.group(0), x[first.start():]).strip()


def fetch():
    """Programme de la page officielle, JOUR PAR JOUR.

    La conference dure deux jours, et la page rend les deux journees dans le
    MEME HTML : deux grilles que des onglets JavaScript montrent a tour de
    role. Mises bout a bout sans marqueur, rien ne distinguait le jour 1 du
    jour 2 - le modele annoncait des sessions du mercredi pour le mardi. On
    decoupe donc sur la fin de chaque grille et on prefixe chacune de
    l'intitule de son onglet ("Jour 1 - 22 Septembre 2026")."""
    try:
        html = requests.get(URL, timeout=TIMEOUT)
        html.raise_for_status()
        html = html.text
        jours = re.findall(r"uc-tab-slider__link[^>]*>\s*([^<]{3,60}?)\s*</a>", html)
        grilles = re.split(r"<!--\s*end Loop Grid\s*-->", html)[:-1] or [html]
        blocs = []
        for i, grille in enumerate(grilles):
            titre = jours[i] if i < len(jours) else f"Jour {i + 1}"
            sessions = _sessions(grille)
            blocs.append(f"== {titre} ==\n"
                         + (sessions or "Aucune session publiee pour l'instant."))
        return "\n".join(blocs)[:MAX_CHARS]
    except Exception as e:
        log.warning("fetch agile en seine: %s", e)
        return ""


def prompt_note(key=None, question=""):
    """Bloc a ajouter au prompt systeme : l'heure qu'il est, puis le programme.
    Chaine vide si la question ne concerne pas Agile en Seine.

    L'heure est indissociable du programme : date_note() (minitel_gpt) ne donne
    que le jour, or sans l'heure "les prochaines conferences" ne veut rien dire.
    Le conteneur tourne en Europe/Paris (TZ + tzdata), donc l'heure locale EST
    l'heure de Paris."""
    if key != PRESET_KEY and not is_question(question):
        return ""
    programme = fetch()
    if not programme:
        return ("\n\n[Information systeme] La page du programme est injoignable "
                "pour le moment : dis-le plutot que d'inventer des sessions.")
    now = datetime.now()
    return (f"\n\n[Information systeme] Il est {now.hour}h{now.minute:02d}, "
            f"heure de Paris.\n"
            "PROGRAMME OFFICIEL RELEVE A L'INSTANT (il fait autorite, c'est ta "
            "seule source sur les sessions) :\n" + programme)
