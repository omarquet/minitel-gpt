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
import json
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import unescape
from pathlib import Path

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
MAX_CHARS = 30000
TIMEOUT = 8

# --- Descriptions de session ---------------------------------------------
# La page du programme ne porte que l'entete des sessions (horaire, titre,
# intervenants, salle) : la description est sur la fiche de chaque session, une
# page par session. Les relever toutes prend une vingtaine de secondes, donc
# jamais pendant qu'un visiteur attend : on les garde dans le volume et on les
# rafraichit en tache de fond.
REST_URL = "https://www.agileenseine.com/wp-json/wp/v2/programme?per_page=100"
DESCRIPTIONS_FILE = Path(__file__).parent.parent / "config" / "aes_descriptions.json"
# La reponse du Minitel plafonne a 600 caracteres : une description de 1200
# caracteres (la mediane) ne peut de toute facon qu'etre resumee. On en garde
# de quoi savoir de quoi parle la session, pas le texte integral - a 50
# sessions, chaque centaine de caracteres pese 5 ko de contexte par question.
DESC_MAX_CHARS = 320
DESC_TTL = 6 * 3600
DESC_WORKERS = 4
# Le programme lui-meme : la page pese 2 Mo, la relever prend ~7 s. La payer a
# chaque question, c'est 7 s d'attente ajoutees a celle du modele, pour un
# horaire de conference qui ne bouge pas toutes les minutes.
PROG_TTL = 300
# Sur la fiche, la description est entre ce titre et la biographie du speaker.
_DESC = re.compile(r"[AÀ] propos de cette session (.*?)"
                   r"(?: Speakers? |Aucun speaker|$)", re.S)
# Chaque carte du programme est un lien vers sa fiche : le slug identifie la
# session, le corps du lien porte l'horaire, le titre, les intervenants, la
# salle, le format et les themes.
_CARTE = re.compile(r'<a[^>]+href="https://www\.agileenseine\.com/programme/'
                    r'([^"/]+)/"(.*?)</a>', re.S)
_refresh_lock = threading.Lock()
_prog_lock = threading.Lock()
_prog_cache = {"releve_le": 0.0, "texte": ""}

_SLOT = re.compile(r"\d{2}:\d{2} - \d{2}:\d{2}")
# Chaque grille embarque ce message d'etat vide, cache en display:none. Recopie
# tel quel, il faisait dire au modele qu'une journee pourtant pleine etait vide.
_EMPTY_NOTICE = re.compile(r"Aucun programme n'est pr[ée]vu[^.]*", re.I)


def is_question(text):
    """La question porte-t-elle sur Agile en Seine ?"""
    t = (text or "").lower()
    return any(k in t for k in KEYWORDS)


def _texte(fragment):
    """HTML -> texte lisible : scripts et styles jetes, balises retirees,
    entites decodees (&rsquo; et &#038; pullulent dans les titres), et le
    message d'etat vide supprime."""
    x = re.sub(r"<script[^>]*>.*?</script>", " ", fragment, flags=re.S | re.I)
    x = re.sub(r"<style[^>]*>.*?</style>", " ", x, flags=re.S | re.I)
    x = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x)).strip()
    return _EMPTY_NOTICE.sub("", unescape(x)).strip()


def _sessions(grille, descriptions):
    """HTML d'une grille -> une session par ligne, description en dessous.

    On itere sur les cartes (chacune est un lien vers sa fiche) plutot que sur
    le texte aplati : c'est ce lien qui donne l'identifiant de la session, donc
    sa description. Aplati, le texte collait aussi la salle d'une session a
    l'horaire de la suivante."""
    lignes = []
    for slug, carte in _CARTE.findall(grille):
        entete = _texte(carte).lstrip("> ").strip()
        if not entete:
            continue
        lignes.append(entete)
        desc = descriptions.get(slug, "")
        if desc:
            lignes.append("   Description : " + desc)
    return "\n".join(lignes)


def _tronque(texte, n=DESC_MAX_CHARS):
    """Coupe sur un mot entier, pour ne pas finir sur une syllabe."""
    if len(texte) <= n:
        return texte
    coupe = texte[:n]
    return coupe[:coupe.rfind(" ")].rstrip(" ,;:") + "..."


def _description(lien):
    """Description relevee sur la fiche d'une session ("" si la page n'en a
    pas encore, ou si elle est injoignable)."""
    try:
        r = requests.get(lien, timeout=TIMEOUT)
        r.raise_for_status()
        m = _DESC.search(_texte(r.text))
        return _tronque(" ".join(m.group(1).split())) if m else ""
    except Exception as e:
        log.warning("fiche %s: %s", lien, e)
        return ""


def _load_descriptions():
    try:
        return json.loads(DESCRIPTIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _refresh_descriptions():
    """Met le cache a jour : la liste REST donne les 50 fiches et leur date de
    derniere modification, on ne rouvre que les nouvelles et les modifiees.
    Tourne en tache de fond, jamais pendant qu'un visiteur attend."""
    if not _refresh_lock.acquire(blocking=False):
        return                              # un rafraichissement tourne deja
    try:
        r = requests.get(REST_URL, timeout=TIMEOUT)
        r.raise_for_status()
        cache = _load_descriptions()
        fiches = {}
        for item in r.json():
            slug = item.get("slug")
            if slug:
                fiches[slug] = (item.get("link", ""), item.get("modified", ""))
        neuves = [(slug, lien) for slug, (lien, modif) in fiches.items()
                  if cache.get(slug, {}).get("modifie") != modif]
        if neuves:
            # Pas de requests.Session partagee entre threads : elle n'est pas
            # sure, et le partage produisait des reponses gzip corrompues.
            with ThreadPoolExecutor(DESC_WORKERS) as ex:
                textes = list(ex.map(lambda n: _description(n[1]), neuves))
            for (slug, _), texte in zip(neuves, textes):
                cache[slug] = {"modifie": fiches[slug][1], "texte": texte}
        # Sessions disparues du programme : on ne garde pas de fiches mortes.
        cache = {k: v for k, v in cache.items() if k in fiches}
        cache["_releve_le"] = time.time()
        tmp = DESCRIPTIONS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(DESCRIPTIONS_FILE)
        log.info("descriptions AES: %d fiches relevees, %d au total",
                 len(neuves), len(cache) - 1)
    except Exception as e:
        log.warning("rafraichissement des descriptions AES: %s", e)
    finally:
        _refresh_lock.release()


def descriptions():
    """{slug: description} depuis le cache du volume, et lance un
    rafraichissement en tache de fond s'il est perime. Ne bloque jamais : au
    tout premier demarrage, la premiere question sort sans description
    plutot que d'attendre une vingtaine de secondes."""
    cache = _load_descriptions()
    if time.time() - cache.get("_releve_le", 0) > DESC_TTL:
        threading.Thread(target=_refresh_descriptions, daemon=True).start()
    return {k: v.get("texte", "") for k, v in cache.items()
            if isinstance(v, dict)}


def fetch():
    """Programme de la page officielle, JOUR PAR JOUR.

    La conference dure deux jours, et la page rend les deux journees dans le
    MEME HTML : deux grilles que des onglets JavaScript montrent a tour de
    role. Mises bout a bout sans marqueur, rien ne distinguait le jour 1 du
    jour 2 - le modele annoncait des sessions du mercredi pour le mardi. On
    decoupe donc sur la fin de chaque grille et on prefixe chacune de
    l'intitule de son onglet ("Jour 1 - 22 Septembre 2026")."""
    try:
        r = requests.get(URL, timeout=TIMEOUT)
        r.raise_for_status()
        html = r.text
        descs = descriptions()
        jours = re.findall(r"uc-tab-slider__link[^>]*>\s*([^<]{3,60}?)\s*</a>", html)
        grilles = re.split(r"<!--\s*end Loop Grid\s*-->", html)[:-1] or [html]
        blocs = []
        for i, grille in enumerate(grilles):
            titre = jours[i] if i < len(jours) else f"Jour {i + 1}"
            sessions = _sessions(grille, descs)
            blocs.append(f"== {titre} ==\n"
                         + (sessions or "Aucune session publiee pour l'instant."))
        return "\n".join(blocs)[:MAX_CHARS]
    except Exception as e:
        log.warning("fetch agile en seine: %s", e)
        return ""


def _refresh_programme():
    texte = fetch()
    if texte:                          # un echec ne remplace pas ce qu'on a
        _prog_cache.update(releve_le=time.time(), texte=texte)


def programme():
    """Programme en cache. Perime, il est reconstruit en tache de fond : le
    visiteur lit la version d'il y a quelques minutes plutot que d'attendre.
    Vide (premier appel apres un demarrage), on le releve sur-le-champ."""
    if time.time() - _prog_cache["releve_le"] > PROG_TTL:
        if not _prog_cache["texte"]:
            _refresh_programme()
        elif _prog_lock.acquire(blocking=False):
            def travail():
                try:
                    _refresh_programme()
                finally:
                    _prog_lock.release()
            threading.Thread(target=travail, daemon=True).start()
    return _prog_cache["texte"]


def prompt_note(key=None, question=""):
    """Bloc a ajouter au prompt systeme : l'heure qu'il est, puis le programme.
    Chaine vide si la question ne concerne pas Agile en Seine.

    L'heure est indissociable du programme : date_note() (minitel_gpt) ne donne
    que le jour, or sans l'heure "les prochaines conferences" ne veut rien dire.
    Le conteneur tourne en Europe/Paris (TZ + tzdata), donc l'heure locale EST
    l'heure de Paris."""
    if key != PRESET_KEY and not is_question(question):
        return ""
    prog = programme()
    if not prog:
        return ("\n\n[Information systeme] La page du programme est injoignable "
                "pour le moment : dis-le plutot que d'inventer des sessions.")
    now = datetime.now()
    return (f"\n\n[Information systeme] Il est {now.hour}h{now.minute:02d}, "
            f"heure de Paris.\n"
            "PROGRAMME OFFICIEL DE LA CONFERENCE (il fait autorite, c'est ta "
            "seule source sur les sessions) :\n" + prog)
