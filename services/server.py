#!/usr/bin/env python3
"""
server.py - point d'entree unique du service (VPS).

Ce fork ne supporte plus le montage Raspberry Pi + port serie de l'origine :
minitel_gpt.py a ete allege de tout ce qui etait specifique au Pi (classe
Term, boucle run() sur /dev/ttyUSB0). Il ne reste que la logique partagee
(ecrans, LLM, lecture des touches) reutilisee ici via WSTerm.

Fait tourner, dans UN SEUL processus Flask :
  - l'interface d'admin existante (admin_ui.app), servie sur "/"
  - un endpoint WebSocket "/ws" qui rejoue la boucle de chat du Minitel,
    mais en lisant / ecrivant les octets Videotex sur la WebSocket.

Cote materiel : un ESP32 relie au Minitel (DIN5, UART 1200 7E1) se connecte a
wss://<domaine>/ws et fait un pont transparent octet-a-octet entre l'UART du
Minitel et la WebSocket. Le meme protocole (frames binaires, octets bruts) est
parlable depuis un navigateur (voir minitel.html), ce qui permet de
tester la chaine complete SANS ESP32.
"""
import os
import re
import sys
import time
import json
import logging
from collections import deque
from contextlib import contextmanager
from itertools import count
from pathlib import Path
from datetime import datetime
from threading import Lock

import requests

# Les fichiers d'origine sont dans services/ ; on s'assure qu'ils sont importables.
sys.path.insert(0, str(Path(__file__).parent))

from flask_sock import Sock
from flask import send_file, request, jsonify

# App Flask d'admin existante (expose `app` au niveau module, port 8080 d'origine).
from admin_ui import app

# On reutilise la logique d'ecran / lecture clavier / appel LLM partagee.
import minitel_gpt as mg
from minitel_gpt import (
    load_preset, call_llm, call_gemini, to_ascii, strip_markdown,
    apply_minitel_markup, visible_len, visible_truncate, wrap,
    show_home, read_question, show_response,
    COLS,
    CR, LF, FF, RS, ESC, SEP, BS,
    FG_CYAN, FG_WHITE,
    SS3_MAP,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [minitel-ws] %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("minitel-ws")

sock = Sock(app)

# URL publique de l'admin, affichee sur le Minitel via la touche GUIDE.
ADMIN_URL = os.getenv("ADMIN_PUBLIC_URL", "")

# Jeton partage protegeant les endpoints WebSocket (appelle une API payante) :
# ?token=... dans l'URL, compare a WS_TOKEN. Si WS_TOKEN est vide, aucune
# verification n'est faite (comportement d'origine, pratique en dev local).
WS_TOKEN = os.getenv("WS_TOKEN", "")


def ws_token_valid():
    return not WS_TOKEN or request.args.get("token") == WS_TOKEN


# --- Dictee depuis un telephone -------------------------------------------
# Chaque connexion a /ws lance sa propre session (run_session) : un telephone
# qui se connecterait a /ws ouvrirait une DEUXIEME conversation au lieu
# d'ecrire dans celle du Minitel. La dictee passe donc par des requetes HTTP
# (/dictee/inject) qui deposent des octets dans la file d'une session deja
# ouverte ; WSTerm.read_byte() les consomme avant la WebSocket, donc
# read_question les recoit comme de vraies frappes.
SESSIONS = {}                   # id croissant -> WSTerm
SESSIONS_LOCK = Lock()
_session_ids = count(1)

# Plafond de la file : borne la memoire et le temps d'ecoulement a l'ecran.
INJECT_MAX_QUEUE = 2000
# Delai entre deux octets injectes : effet machine a ecrire, et de toute facon
# le Minitel affiche a 1200 bauds (~120 car/s), inutile d'aller plus vite.
INJECT_DELAY = 0.03
# Taille max d'une requete de dictee (caracteres, et nombre d'effacements).
DICTEE_MAX_TEXT = 1000


def latest_session():
    """La session la plus recemment ouverte. Le serveur accepte plusieurs
    Minitels (un thread chacun), mais une dictee ne peut viser qu'un ecran :
    on prend le dernier connecte, celui qu'on vient de brancher."""
    with SESSIONS_LOCK:
        if not SESSIONS:
            return None
        return SESSIONS[max(SESSIONS)]


# Un LLM ne connait pas la date du jour par lui-meme : pour les presets figes
# dans le temps (ex. annees80bis), on lui fournit le jour/mois reels ramenes
# a l'annee configuree via le champ optionnel "fixed_year" du preset actif.
# FALLBACK_FIXED_YEARS couvre les presets existants dont le prompts.json deja
# deploye (volume du serveur) n'a pas encore ce champ.
MOIS_FR = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet",
           "aout", "septembre", "octobre", "novembre", "decembre"]
FALLBACK_FIXED_YEARS = {"annees80": 1989, "annees80bis": 1989}


def active_preset_raw():
    try:
        with open(mg.PROMPTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data["active"], data["presets"].get(data["active"], {})
    except Exception:
        return None, {}


def with_fixed_date(system_prompt):
    key, preset = active_preset_raw()
    fixed_year = preset.get("fixed_year") or FALLBACK_FIXED_YEARS.get(key)
    if not fixed_year:
        return system_prompt
    now = datetime.now()
    # "le 1 septembre" n'existe pas en francais, et le modele recopie ce qu'il
    # lit : le premier du mois s'ecrit "1er".
    jour = "1er" if now.day == 1 else str(now.day)
    date_str = f"{jour} {MOIS_FR[now.month - 1]} {fixed_year}"
    return system_prompt + f"\n\n[Information systeme] Nous sommes aujourd'hui le {date_str}."


# Seule exception ou le personnage a le droit de regarder sur le net en
# temps reel : le programme d'Agile en Seine, qui change jusqu'au dernier
# moment. Pas de tool-calling generique, juste ce cas precis, en dur.
AGILE_EN_SEINE_URL = "https://www.agileenseine.com/programme-2026/"
AGILE_EN_SEINE_KEYWORDS = ("agile en seine", "agileenseine", "aes")
# Plafond du contenu injecte, aligne sur celui des fichiers de connaissance.
# A 4000 caracteres, plus de la moitie du programme etait coupee (42 creneaux
# horaires conserves sur 100) et la troncature tombait en plein titre de
# session : le modele repondait alors a cote sur les conferences de fin de
# journee, sans rien signaler.
AGILE_EN_SEINE_MAX_CHARS = 12000


def is_agile_en_seine_question(text):
    t = text.lower()
    return any(k in t for k in AGILE_EN_SEINE_KEYWORDS)


def fetch_agile_en_seine_context():
    try:
        r = requests.get(AGILE_EN_SEINE_URL, timeout=5)
        r.raise_for_status()
        html = re.sub(r"<script[^>]*>.*?</script>", " ", r.text, flags=re.S | re.I)
        html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:AGILE_EN_SEINE_MAX_CHARS]
    except Exception as e:
        log.warning("fetch agile en seine: %s", e)
        return ""


class WSClosed(Exception):
    """Levee quand la WebSocket est fermee (ESP32 eteint, navigateur ferme...)."""


class WSTerm:
    """Meme interface publique que la classe Term de minitel_gpt
    (w / clear / line / center / read_byte / read_key), mais les octets
    circulent sur la WebSocket au lieu du port serie."""

    def __init__(self, ws):
        self.ws = ws
        self._buf = bytearray()
        # Dictee : octets deposes par /dictee/inject, consommes avant la
        # WebSocket. `awaiting_input` n'est vrai que pendant read_question.
        self._inject = deque()
        self._inject_lock = Lock()
        self._inject_next_at = 0.0
        self.awaiting_input = False

    # ----- ecriture (Pi -> Minitel) -----
    def w(self, data):
        if isinstance(data, str):
            data = to_ascii(data).encode("ascii", errors="replace")
        else:
            data = bytes(data)
        try:
            self.ws.send(data)              # frame binaire
        except Exception as e:
            raise WSClosed(str(e))

    def clear(self):
        self.w(bytes([FF, RS]))
        time.sleep(0.05)

    def line(self, text=""):
        self.w(visible_truncate(text, COLS)); self.w(bytes([CR, LF]))

    def center(self, text):
        text = visible_truncate(text, COLS)
        self.w(" " * ((COLS - visible_len(text)) // 2)); self.w(text); self.w(bytes([CR, LF]))

    # ----- dictee (telephone -> Minitel) -----
    @contextmanager
    def injection_allowed(self):
        """Fenetre pendant laquelle la file d'injection s'ecoule : uniquement
        read_question. show_response ignore les caracteres et les afficher
        ecraserait la page affichee ; les octets dictes pendant la lecture
        d'une reponse restent donc en file et partent a la saisie suivante."""
        self.awaiting_input = True
        try:
            yield
        finally:
            self.awaiting_input = False

    def inject(self, data):
        """Depose des octets a lire comme s'ils venaient du clavier.
        Retourne le nombre reellement mis en file (le reste est jete)."""
        with self._inject_lock:
            data = bytes(data)[:max(0, INJECT_MAX_QUEUE - len(self._inject))]
            self._inject.extend(data)
        return len(data)

    def pending_injection(self):
        with self._inject_lock:
            return len(self._inject)

    def _pop_injected(self):
        now = time.time()
        with self._inject_lock:
            if not self._inject or not self.awaiting_input or now < self._inject_next_at:
                return None
            b = self._inject.popleft()
            self._inject_next_at = now + INJECT_DELAY
        # ECHO : read_question ne re-echoie pas les frappes, c'est le Minitel
        # qui affiche en local ce qu'on tape sur son clavier. Un caractere
        # dicte depuis le telephone n'a jamais ete tape : sans cet echo il
        # entrerait dans la question sans que rien ne s'affiche. On n'echoie
        # que l'imprimable : BS est deja rendu par read_question (backspace
        # destructif) et CR vaut ENVOI (le CR LF est ecrit par l'appelant).
        if 0x20 <= b <= 0x7E:
            self.w(bytes([b]))
        return b

    def _recv_timeout(self):
        """0.1 s d'ordinaire ; raccourci quand une dictee attend son tour, pour
        rendre la main a l'heure sans cesser d'ecouter le clavier physique."""
        with self._inject_lock:
            if not self._inject or not self.awaiting_input:
                return 0.1
            remaining = self._inject_next_at - time.time()
        return max(0.01, min(0.1, remaining))

    # ----- lecture (Minitel -> Pi) -----
    def read_byte(self):
        """Non bloquant ~0.1 s, comme Term.read_byte (serie timeout=0.1).

        Les octets dictes depuis le telephone passent avant la WebSocket, au
        rythme de INJECT_DELAY. Ils ne s'intercalent jamais au milieu d'une
        sequence deja entamee (SEP + code) tant qu'elle est arrivee dans la
        meme frame, self._buf gardant la priorite."""
        if not self._buf:
            b = self._pop_injected()
            if b is not None:
                return b
            try:
                msg = self.ws.receive(timeout=self._recv_timeout())
            except Exception as e:
                raise WSClosed(str(e))
            if msg is None:                # timeout : rien recu ce cycle
                return None
            if isinstance(msg, str):
                msg = to_ascii(msg).encode("ascii", errors="ignore")
            if not msg:                    # frame vide -> fermeture cote client
                raise WSClosed("empty frame")
            self._buf.extend(msg)
        return self._buf.pop(0)

    # Copie fidele de Term.read_key (ne depend que de read_byte).
    def read_key(self, timeout):
        end = time.time() + timeout
        while time.time() < end:
            b = self.read_byte()
            if b is None:
                continue
            if b == SEP:                    # Minitel 1 : SEP + code
                code = self.read_byte(); t2 = time.time() + 0.5
                while code is None and time.time() < t2:
                    code = self.read_byte()
                if code is None:
                    continue
                return ('fn', code)
            if b == ESC:                    # Minitel 2 : VT100 "ESC O x"
                b2 = self.read_byte(); t2 = time.time() + 0.5
                while b2 is None and time.time() < t2:
                    b2 = self.read_byte()
                if b2 == 0x4F:
                    code = self.read_byte(); t3 = time.time() + 0.5
                    while code is None and time.time() < t3:
                        code = self.read_byte()
                    if code in SS3_MAP:
                        return ('fn', SS3_MAP[code])
                continue
            return ('char', b)
        return ('timeout', None)


def show_guide_ws(t):
    """Ecran GUIDE : choix de la personnalite active (touche numerique), plus
    l'URL de l'admin en bas d'ecran. Contrairement au Pi d'origine, /ws est
    accessible publiquement : on n'affiche jamais le mot de passe admin ici
    (n'importe qui pourrait l'obtenir en appuyant sur GUIDE)."""
    try:
        with open(mg.PROMPTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"active": None, "presets": {}}
    keys = list(data.get("presets", {}).keys())[:9]
    active = data.get("active")

    t.clear()
    t.w(bytes([CR, LF]))
    t.w(FG_CYAN); t.center("=== CHOISIR UNE PERSONNALITE ===")
    t.w(bytes([CR, LF, CR, LF])); t.w(FG_WHITE)
    for i, k in enumerate(keys, start=1):
        label = data["presets"][k].get("label", k)
        marker = " (active)" if k == active else ""
        t.line((f"{i}. {label}{marker}")[:COLS])
    t.w(bytes([CR, LF]))
    t.w(FG_CYAN); t.center("Tapez un chiffre pour changer,")
    t.center("une autre touche pour revenir")
    t.w(bytes([CR, LF, CR, LF]))
    t.w(FG_WHITE)
    if ADMIN_URL:
        t.center("Admin :")
        for chunk in wrap(ADMIN_URL):       # une URL peut depasser 40 colonnes
            t.center(chunk)

    kind, code = t.read_key(60)
    if kind == 'char' and 0x31 <= code <= 0x39:   # '1'..'9'
        idx = code - 0x31
        if idx < len(keys) and keys[idx] != active:
            data["active"] = keys[idx]
            with open(mg.PROMPTS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            t.clear()
            t.w(FG_CYAN); t.center("Personnalite activee :")
            t.center(data["presets"][keys[idx]].get("label", keys[idx]))
            time.sleep(1.5)


def run_session(t):
    """Boucle de conversation, pilotee par un WSTerm. call_llm() (minitel_gpt)
    aiguille lui-meme vers Mistral/Claude/Gemini selon LLM_PROVIDER."""
    while True:                             # boucle sommaire
        system_prompt, title_msg, question_msg, loading_msg, logo = load_preset()
        system_prompt = with_fixed_date(system_prompt)
        history = []
        show_home(t, title_msg, question_msg, logo)

        while True:                         # boucle conversation
            with t.injection_allowed():     # fenetre d'ecoulement de la dictee
                question, action = read_question(t)
            if action == 'guide':
                show_guide_ws(t); break
            if action in ('repetition', 'retour'):
                last = next((h["content"] for h in reversed(history)
                             if h["role"] == "assistant"), None)
                if last is None:
                    t.w(bytes([CR, LF]))   # sinon le prochain "> " s'accole au precedent
                    continue
                start_at_last = (action == 'retour')
                if show_response(t, apply_minitel_markup(last), start_at_last) in ('sommaire', 'timeout'):
                    break
                t.clear()
                t.w(FG_WHITE)
                t.center("Repondez ou SOMMAIRE pour finir")
                t.w(bytes([CR, LF]))
                continue
            if action in ('sommaire', 'timeout'):
                break

            history.append({"role": "user", "content": question})
            log.info("Q: %r", question)

            t.w(bytes([CR, LF])); t.w(FG_CYAN); t.line(""); t.center(loading_msg)
            call_prompt = system_prompt
            if is_agile_en_seine_question(question):
                ctx = fetch_agile_en_seine_context()
                if ctx:
                    call_prompt += ("\n\nCONTENU ACTUEL DE LA PAGE DU PROGRAMME (extrait "
                                     "brut, utilise ces infos pour repondre precisement) "
                                     ":\n" + ctx)
            try:
                answer = to_ascii(strip_markdown(call_llm(call_prompt, history)))
                history.append({"role": "assistant", "content": answer})
            except Exception as e:
                log.error("API: %s", e)
                answer = "Erreur de connexion. Reessayez."

            if show_response(t, apply_minitel_markup(answer)) in ('sommaire', 'timeout'):
                break

            t.w(bytes([CR, LF])); t.w(FG_WHITE)
            t.center("Repondez ou SOMMAIRE pour finir")
            t.w(bytes([CR, LF]))

            if len(history) > 20:
                history = history[-20:]


@sock.route("/ws")
def ws_minitel(ws):
    if not ws_token_valid():
        log.warning("Connexion /ws refusee (token invalide)")
        return
    log.info("Minitel connecte (WebSocket)")
    t = WSTerm(ws)
    sid = next(_session_ids)
    with SESSIONS_LOCK:                     # visible par /dictee/inject
        SESSIONS[sid] = t
    try:
        run_session(t)
    except WSClosed:
        log.info("Minitel deconnecte")
    except Exception as e:
        log.exception("Session terminee sur erreur: %s", e)
    finally:
        with SESSIONS_LOCK:
            SESSIONS.pop(sid, None)


@sock.route("/ws-echo")
def ws_echo(ws):
    if not ws_token_valid():
        log.warning("Connexion /ws-echo refusee (token invalide)")
        return
    log.info("Echo client connecte")
    try:
        while True:
            msg = ws.receive()
            if msg is None:
                break
            if isinstance(msg, str):
                ws.send(f"echo: {msg}")
            else:
                ws.send(msg)
    except Exception as e:
        log.info("Echo client deconnecte: %s", e)


@sock.route("/ws-gemini")
def ws_gemini(ws):
    if not ws_token_valid():
        log.warning("Connexion /ws-gemini refusee (token invalide)")
        return
    log.info("Client Gemini connecte")
    history = []
    system_prompt = "Tu es un assistant concis, utile et amical. Réponds en français."
    try:
        while True:
            msg = ws.receive()
            if msg is None:
                break
            if isinstance(msg, (bytes, bytearray)):
                text = bytes(msg).decode("utf-8", errors="ignore")
            else:
                text = str(msg)
            text = text.strip()
            if not text:
                continue

            history.append({"role": "user", "content": text})
            try:
                answer = strip_markdown(call_gemini(system_prompt, history))
                history.append({"role": "assistant", "content": answer})
                payload = bytes([FF, RS]) + b"GEMINI> " + answer.encode("ascii", errors="replace") + bytes([CR, LF])
                ws.send(payload)
            except Exception as exc:
                log.exception("Erreur Gemini: %s", exc)
                payload = bytes([FF, RS]) + f"ERREUR GEMINI: {exc}".encode("ascii", errors="replace") + bytes([CR, LF])
                ws.send(payload)
    except Exception as e:
        log.info("Client Gemini deconnecte: %s", e)


def _printable(text):
    """Meme filtre que read_question : ASCII imprimable seulement. Les sauts
    de ligne deviennent des espaces (read_question traite CR et LF comme
    ENVOI : un retour a la ligne dicte validerait la phrase en plein
    milieu)."""
    text = re.sub(r"[\r\n\t]+", " ", text)
    return "".join(c for c in to_ascii(text) if 0x20 <= ord(c) <= 0x7E)


@app.route("/dictee")
@app.route("/dictee.html")          # alias : minitel.html garde son extension
def dictee_page():
    """Page de dictee pour telephone.

    Seule route de la dictee a ne PAS exiger le jeton : il ne doit pas rester
    dans l'URL, le telephone passant de main en main (barre d'adresse,
    historique Safari, partage de lien). La page ne contient aucun secret et
    ne sert a rien sans jeton, qui est saisi une fois puis garde dans le
    localStorage du telephone ; ce sont /dictee/status et /dictee/inject qui
    le verifient a chaque appel."""
    html_path = Path(__file__).resolve().parent.parent / "dictee.html"
    return send_file(html_path, mimetype="text/html")


@app.route("/dictee/status")
def dictee_status():
    if not ws_token_valid():
        return jsonify(error="token"), 403
    t = latest_session()
    return jsonify(sessions=len(SESSIONS),
                   ready=bool(t and t.awaiting_input),
                   pending=t.pending_injection() if t else 0)


@app.route("/dictee/inject", methods=["POST"])
def dictee_inject():
    """Injecte des frappes dans la session Minitel en cours.
    Corps JSON : {"back": n, "text": "...", "cr": true} - `back` efface n
    caracteres deja affiches (le telephone envoie un diff, iOS reecrivant ses
    phrases pendant la dictee), `cr` vaut ENVOI."""
    if not ws_token_valid():
        return jsonify(error="token"), 403
    t = latest_session()
    if t is None:
        return jsonify(ok=False, error="no_session"), 409

    body = request.get_json(silent=True) or {}
    try:
        back = max(0, min(int(body.get("back") or 0), DICTEE_MAX_TEXT))
    except (TypeError, ValueError):
        back = 0
    out = bytearray([BS]) * back
    out += _printable(str(body.get("text") or ""))[:DICTEE_MAX_TEXT].encode("ascii")
    if body.get("cr"):
        out.append(CR)

    queued = t.inject(out)
    if queued:
        log.info("Dictee: %d octet(s) injecte(s)%s", queued, " + ENVOI" if body.get("cr") else "")
    return jsonify(ok=True, queued=queued, dropped=len(out) - queued,
                   ready=t.awaiting_input, sessions=len(SESSIONS))


@app.route("/healthz")
def healthz():
    return "ok", 200


@app.route("/minitel.html")
def minitel_test():
    html_path = Path(__file__).resolve().parent.parent / "minitel.html"
    return send_file(html_path, mimetype="text/html")


if __name__ == "__main__":
    # Fallback developpement local. En prod, on lance gunicorn (voir Dockerfile).
    app.run(host="0.0.0.0", port=8080, threaded=True)
