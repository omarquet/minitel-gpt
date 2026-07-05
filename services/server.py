#!/usr/bin/env python3
"""
server.py - point d'entree unique pour le deploiement VPS / Coolify.

Fait tourner, dans UN SEUL processus Flask :
  - l'interface d'admin existante (admin_ui.app), inchangee, servie sur "/"
  - un endpoint WebSocket "/ws" qui rejoue la boucle de chat du Minitel,
    mais en lisant / ecrivant les octets Videotex sur la WebSocket au lieu
    du port serie local /dev/ttyUSB0.

Cote materiel : un ESP32 relie au Minitel (DIN5, UART 1200 7E1) se connecte a
wss://<domaine>/ws et fait un pont transparent octet-a-octet entre l'UART du
Minitel et la WebSocket. Le meme protocole (frames binaires, octets bruts) est
parlable depuis un navigateur (voir minitel-emulator.html), ce qui permet de
tester la chaine complete SANS ESP32.

Ce fichier est purement ADDITIF : il ne modifie ni admin_ui.py ni
minitel_chatgpt.py, ce qui garde le depot a jour facilement (git pull).
"""
import os
import re
import sys
import time
import json
import logging
from pathlib import Path
from datetime import datetime

import requests

# Les fichiers d'origine sont dans services/ ; on s'assure qu'ils sont importables.
sys.path.insert(0, str(Path(__file__).parent))

from flask_sock import Sock
from flask import send_file, request

# App Flask d'admin existante (expose `app` au niveau module, port 8080 d'origine).
from admin_ui import app

# On reutilise TOUTE la logique d'ecran / boucle du terminal d'origine.
# minitel_chatgpt importe `serial` mais ne l'ouvre qu'a l'instanciation de Term,
# ce qu'on ne fait jamais ici : l'import est donc sans risque sur un VPS.
import minitel_chatgpt as mg
from minitel_chatgpt import (
    load_preset, call_llm, to_ascii, wrap,
    show_home, show_response,
    COLS, IDLE_TIMEOUT,
    CR, LF, FF, RS, ESC, SEP, BS,
    FG_CYAN, FG_WHITE, FG_GREEN,
    K_ENVOI, K_RETOUR, K_CORR, K_GUIDE, K_SOMMAIRE, K_ANNUL, K_REPET,
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
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mistral").strip().lower()
GEMINI_API_KEY = os.getenv("GEMINI_KEY") or os.getenv("GEMINI_API_KEY") or ""
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# Jeton partage protegeant les endpoints WebSocket (appelle une API payante) :
# ?token=... dans l'URL, compare a WS_TOKEN. Si WS_TOKEN est vide, aucune
# verification n'est faite (comportement d'origine, pratique en dev local).
WS_TOKEN = os.getenv("WS_TOKEN", "")


def ws_token_valid():
    return not WS_TOKEN or request.args.get("token") == WS_TOKEN


# Un LLM ne connait pas la date du jour par lui-meme : pour les presets figes
# dans le temps (ex. annees80bis), on lui fournit le jour/mois reels ramenes
# a l'annee configuree via le champ optionnel "fixed_year" du preset actif.
# FALLBACK_FIXED_YEARS couvre les presets existants dont le prompts.json deja
# deploye (volume Coolify) n'a pas encore ce champ.
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
    date_str = f"{now.day} {MOIS_FR[now.month - 1]} {fixed_year}"
    return system_prompt + f"\n\n[Information systeme] Nous sommes aujourd'hui le {date_str}."


# Seule exception ou le personnage a le droit de regarder sur le net en
# temps reel : le programme d'Agile en Seine, qui change jusqu'au dernier
# moment. Pas de tool-calling generique, juste ce cas precis, en dur.
AGILE_EN_SEINE_URL = "https://www.agileenseine.com/programme-2026/"
AGILE_EN_SEINE_KEYWORDS = ("agile en seine", "agileenseine")


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
        return text[:4000]
    except Exception as e:
        log.warning("fetch agile en seine: %s", e)
        return ""


class WSClosed(Exception):
    """Levee quand la WebSocket est fermee (ESP32 eteint, navigateur ferme...)."""


class WSTerm:
    """Meme interface publique que la classe Term de minitel_chatgpt
    (w / clear / line / center / read_byte / read_key), mais les octets
    circulent sur la WebSocket au lieu du port serie."""

    def __init__(self, ws):
        self.ws = ws
        self._buf = bytearray()

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
        self.w(text[:COLS]); self.w(bytes([CR, LF]))

    def center(self, text):
        text = text[:COLS]
        self.w(" " * ((COLS - len(text)) // 2)); self.w(text); self.w(bytes([CR, LF]))

    # ----- lecture (Minitel -> Pi) -----
    def read_byte(self):
        """Non bloquant ~0.1 s, comme Term.read_byte (serie timeout=0.1)."""
        if not self._buf:
            try:
                msg = self.ws.receive(timeout=0.1)
            except Exception as e:
                raise WSClosed(str(e))
            if msg is None:                # timeout : rien recu ce cycle
                return None
            if isinstance(msg, str):
                msg = msg.encode("ascii", errors="ignore")
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


def read_question_ws(t):
    """Copie de read_question() de minitel_chatgpt.py, avec en plus ANNULATION
    (efface toute la phrase en cours) et REPETITION (redemande le dernier
    message affiche). Duplique la logique plutot que de toucher au fichier
    d'origine."""
    t.w(FG_GREEN)
    t.w("> ")
    buf = []
    while True:
        kind, code = t.read_key(IDLE_TIMEOUT)
        if kind == 'timeout':
            return None, 'timeout'
        if kind == 'fn':
            if code == K_SOMMAIRE:
                return None, 'sommaire'
            if code == K_GUIDE:
                return None, 'guide'
            if code == K_ENVOI:
                if buf:
                    return "".join(buf), 'envoi'
            if code in (K_CORR, K_RETOUR):
                if buf:
                    buf.pop()
                    t.w(bytes([BS, 0x20, BS]))
            if code == K_ANNUL:
                if buf:
                    t.w(bytes([BS, 0x20, BS]) * len(buf))
                    buf.clear()
            if code == K_REPET:
                return None, 'repetition'
            continue
        c = code
        if c in (CR, LF):
            if buf:
                return "".join(buf), 'envoi'
        elif c in (BS, 0x7F):
            if buf:
                buf.pop()
                t.w(bytes([BS, 0x20, BS]))
        elif 0x20 <= c <= 0x7E:
            buf.append(chr(c))


def show_guide_ws(t):
    """Ecran GUIDE : sur un VPS l'IP locale n'a pas de sens, on affiche l'URL
    publique de l'admin (ADMIN_PUBLIC_URL). Contrairement au Pi d'origine,
    /ws est accessible publiquement : on n'affiche jamais le mot de passe
    admin ici (n'importe qui pourrait l'obtenir en appuyant sur GUIDE)."""
    t.clear()
    t.w(bytes([CR, LF, CR, LF]))
    t.w(FG_CYAN); t.center("=== ADMINISTRATION ===")
    t.w(bytes([CR, LF, CR, LF])); t.w(FG_WHITE)
    if ADMIN_URL:
        for chunk in wrap(ADMIN_URL):       # une URL peut depasser 40 colonnes
            t.center(chunk)
    else:
        t.center("Voir Coolify pour l'URL admin")
    t.w(bytes([CR, LF, CR, LF, CR, LF]))
    t.w(FG_CYAN); t.center("Une touche pour revenir")
    t.read_key(120)


def call_gemini(system_prompt, history):
    """Appelle l'API Gemini via HTTP brut sans toucher aux fichiers d'origine."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_KEY/GEMINI_API_KEY non configure")

    contents = []
    if system_prompt:
        contents.append({
            "role": "user",
            "parts": [{"text": f"[System]\n{system_prompt}"}],
        })
    for item in history:
        role = "user" if item.get("role") == "user" else "model"
        contents.append({
            "role": role,
            "parts": [{"text": item.get("content", "")}],
        })

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    response = requests.post(
        url,
        json={"contents": contents, "generationConfig": {"maxOutputTokens": 700}},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    parts = []
    for candidate in data.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            text = part.get("text", "") if isinstance(part, dict) else ""
            if text:
                parts.append(text)
    if parts:
        return "".join(parts).strip()
    raise RuntimeError(f"Reponse Gemini non exploitable: {data}")


def run_session(t):
    """Reprend la boucle de run() d'origine, mais pilotee par un WSTerm."""
    llm_fn = call_gemini if LLM_PROVIDER == "gemini" else call_llm

    while True:                             # boucle sommaire
        system_prompt, title_msg, question_msg, loading_msg = load_preset()
        system_prompt = with_fixed_date(system_prompt)
        history = []
        show_home(t, title_msg, question_msg)

        while True:                         # boucle conversation
            question, action = read_question_ws(t)
            if action == 'guide':
                show_guide_ws(t); break
            if action == 'repetition':
                last = next((h["content"] for h in reversed(history)
                             if h["role"] == "assistant"), None)
                if last is None:
                    continue
                if show_response(t, last) in ('sommaire', 'timeout'):
                    break
                t.w(bytes([CR, LF])); t.w(FG_WHITE)
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
                answer = to_ascii(llm_fn(call_prompt, history))
                history.append({"role": "assistant", "content": answer})
            except Exception as e:
                log.error("API: %s", e)
                answer = "Erreur de connexion. Reessayez."

            if show_response(t, answer) in ('sommaire', 'timeout'):
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
    try:
        run_session(WSTerm(ws))
    except WSClosed:
        log.info("Minitel deconnecte")
    except Exception as e:
        log.exception("Session terminee sur erreur: %s", e)


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
                answer = call_gemini(system_prompt, history)
                history.append({"role": "assistant", "content": answer})
                payload = bytes([FF, RS]) + b"GEMINI> " + answer.encode("ascii", errors="replace") + bytes([CR, LF])
                ws.send(payload)
            except Exception as exc:
                log.exception("Erreur Gemini: %s", exc)
                payload = bytes([FF, RS]) + f"ERREUR GEMINI: {exc}".encode("ascii", errors="replace") + bytes([CR, LF])
                ws.send(payload)
    except Exception as e:
        log.info("Client Gemini deconnecte: %s", e)


@app.route("/healthz")
def healthz():
    return "ok", 200


@app.route("/minitel-emulator.html")
def minitel_emulator():
    html_path = Path(__file__).resolve().parent.parent / "minitel-emulator.html"
    return send_file(html_path, mimetype="text/html")


@app.route("/minitel-emulator.minimal.html")
def minitel_emulator_minimal():
    html_path = Path(__file__).resolve().parent.parent / "minitel-emulator.minimal.html"
    return send_file(html_path, mimetype="text/html")


@app.route("/minitel-emulator.simple.html")
def minitel_emulator_simple():
    html_path = Path(__file__).resolve().parent.parent / "minitel-emulator.simple.html"
    return send_file(html_path, mimetype="text/html")


@app.route("/minitel-echo-test.html")
def minitel_echo_test():
    html_path = Path(__file__).resolve().parent.parent / "minitel-echo-test.html"
    return send_file(html_path, mimetype="text/html")


@app.route("/minitel-test.html")
def minitel_test():
    html_path = Path(__file__).resolve().parent.parent / "minitel-test.html"
    return send_file(html_path, mimetype="text/html")


if __name__ == "__main__":
    # Fallback developpement local. En prod, on lance gunicorn (voir Dockerfile).
    app.run(host="0.0.0.0", port=8080, threaded=True)
