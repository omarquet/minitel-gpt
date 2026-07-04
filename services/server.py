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
import sys
import time
import logging
from pathlib import Path

# Les fichiers d'origine sont dans services/ ; on s'assure qu'ils sont importables.
sys.path.insert(0, str(Path(__file__).parent))

from flask_sock import Sock

# App Flask d'admin existante (expose `app` au niveau module, port 8080 d'origine).
from admin_ui import app

# On reutilise TOUTE la logique d'ecran / boucle du terminal d'origine.
# minitel_chatgpt importe `serial` mais ne l'ouvre qu'a l'instanciation de Term,
# ce qu'on ne fait jamais ici : l'import est donc sans risque sur un VPS.
import minitel_chatgpt as mg
from minitel_chatgpt import (
    load_preset, call_llm, to_ascii, wrap,
    show_home, read_question, show_response,
    COLS, IDLE_TIMEOUT,
    CR, LF, FF, RS, ESC, SEP,
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


def show_guide_ws(t):
    """Ecran GUIDE : sur un VPS l'IP locale n'a pas de sens, on affiche l'URL
    publique de l'admin (ADMIN_PUBLIC_URL)."""
    t.clear()
    t.w(bytes([CR, LF, CR, LF]))
    t.w(FG_CYAN); t.center("=== ADMINISTRATION ===")
    t.w(bytes([CR, LF, CR, LF])); t.w(FG_WHITE)
    if ADMIN_URL:
        for chunk in wrap(ADMIN_URL):       # une URL peut depasser 40 colonnes
            t.center(chunk)
    else:
        t.center("Voir Coolify pour l'URL admin")
    t.w(bytes([CR, LF, CR, LF]))
    t.center("Mot de passe : " + os.getenv("ADMIN_PASSWORD", "mistral"))
    t.w(bytes([CR, LF, CR, LF, CR, LF]))
    t.w(FG_CYAN); t.center("Une touche pour revenir")
    t.read_key(120)


def run_session(t):
    """Reprend la boucle de run() d'origine, mais pilotee par un WSTerm."""
    while True:                             # boucle sommaire
        system_prompt, title_msg, question_msg, loading_msg = load_preset()
        history = []
        show_home(t, title_msg, question_msg)

        while True:                         # boucle conversation
            question, action = read_question(t)
            if action == 'guide':
                show_guide_ws(t); break
            if action in ('sommaire', 'timeout'):
                break

            history.append({"role": "user", "content": question})
            log.info("Q: %r", question)

            t.w(bytes([CR, LF])); t.w(FG_CYAN); t.line(""); t.center(loading_msg)
            try:
                answer = to_ascii(call_llm(system_prompt, history))
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
    log.info("Minitel connecte (WebSocket)")
    try:
        run_session(WSTerm(ws))
    except WSClosed:
        log.info("Minitel deconnecte")
    except Exception as e:
        log.exception("Session terminee sur erreur: %s", e)


@app.route("/healthz")
def healthz():
    return "ok", 200


if __name__ == "__main__":
    # Fallback developpement local. En prod, on lance gunicorn (voir Dockerfile).
    app.run(host="0.0.0.0", port=8080, threaded=True)
