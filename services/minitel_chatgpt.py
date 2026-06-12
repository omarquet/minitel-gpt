#!/usr/bin/env python3
"""
MINITEL GPT — service de chat années 80 sur Minitel.
Interface : sommaire (titre ASCII + invite) → saisie → réponse paginée → re-saisie.
Touches : ENVOI = valider, SUITE = page suivante, SOMMAIRE = retour accueil.
Timeout 5 min sans action → retour sommaire.
"""
import json
import os
import sys
import time
import logging
from pathlib import Path
from dotenv import load_dotenv
import anthropic

load_dotenv(Path(__file__).parent.parent / ".env")

import serial

# ── Config ───────────────────────────────────────────────────────────────
def detect_port():
    for p in ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyAMA0", "/dev/serial0"]:
        if os.path.exists(p):
            return p
    return "/dev/ttyUSB0"

PORT = detect_port()
BAUD = 1200
COLS = 40
SCREEN_ROWS = 24
CONTENT_ROWS = 18          # lignes de contenu par page de réponse
IDLE_TIMEOUT = 300         # 5 min → retour sommaire

ANTHROPIC_KEY = os.environ["ANTHROPIC_KEY"]
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
PROMPTS_FILE = Path(__file__).parent.parent / "config" / "prompts.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [minitel-gpt] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("/home/minitel/minitel-gpt/logs/chatgpt.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── Codes Videotex ───────────────────────────────────────────────────────
ESC, SO, SI, RS, FF, CR, LF, SEP, BS = 0x1B,0x0E,0x0F,0x1E,0x0C,0x0D,0x0A,0x13,0x08
FG_WHITE  = bytes([ESC,0x47])
FG_CYAN   = bytes([ESC,0x46])
FG_YELLOW = bytes([ESC,0x43])
FG_GREEN  = bytes([ESC,0x42])
BG_BLACK  = bytes([ESC,0x50])
DBL_HEIGHT= bytes([ESC,0x4C])   # double hauteur
DBL_SIZE  = bytes([ESC,0x4F])   # double hauteur+largeur
SZ_NORMAL = bytes([ESC,0x4C-0x0C])  # 0x40 normal (placeholder)

# Touches de fonction Minitel (SEP + code)
K_ENVOI=0x41; K_RETOUR=0x42; K_REPET=0x43; K_GUIDE=0x44
K_ANNUL=0x45; K_SOMMAIRE=0x46; K_CORR=0x47; K_SUITE=0x48

FALLBACK_PROMPT = (
    "Tu es MINITEL GPT. Reponds en francais, concis (max 30 lignes de 40 caracteres), "
    "ASCII sans accents ni emojis. Ne mentionne jamais que tu es une autre IA."
)


def load_preset():
    """Retourne (system, title_msg, question_msg, loading_msg)."""
    try:
        data = json.load(open(PROMPTS_FILE))
        p = data["presets"][data["active"]]
        return (
            p.get("system", FALLBACK_PROMPT),
            p.get("title_msg", "*** MINITEL GPT ***"),
            p.get("question_msg", "Posez votre question :"),
            p.get("loading_msg", "Consultation en cours..."),
        )
    except Exception as e:
        log.warning(f"prompts.json: {e}")
        return (FALLBACK_PROMPT, "*** MINITEL GPT ***",
                "Posez votre question :", "Consultation en cours...")


# ── ASCII title (pyfiglet) ───────────────────────────────────────────────
def build_title():
    try:
        from pyfiglet import Figlet
        lines = []
        for word, font in [("MINITEL", "small"), ("GPT", "standard")]:
            fig = Figlet(font=font, width=COLS)
            for ln in fig.renderText(word).rstrip("\n").split("\n"):
                if ln.strip():
                    lines.append(ln[:COLS])
        return lines
    except Exception as e:
        log.warning(f"pyfiglet: {e}")
        return ["", "    M I N I T E L   G P T", ""]

TITLE_LINES = build_title()


# ── Serial helpers ───────────────────────────────────────────────────────
class Term:
    def __init__(self):
        self.s = serial.Serial(PORT, BAUD, bytesize=7, parity="E",
                               stopbits=1, timeout=0.1)
        time.sleep(0.3)

    def w(self, data):
        if isinstance(data, str):
            data = data.encode("ascii", errors="replace")
        self.s.write(data)

    def clear(self):
        self.w(bytes([FF, RS]))
        time.sleep(0.2)

    def line(self, text=""):
        self.w(text[:COLS]); self.w(bytes([CR, LF]))

    def center(self, text):
        text = text[:COLS]
        self.w(" " * ((COLS - len(text)) // 2)); self.w(text); self.w(bytes([CR, LF]))

    def read_byte(self):
        b = self.s.read(1)
        return b[0] if b else None

    def read_key(self, timeout):
        """Lit une touche. Retourne ('char', c) ou ('fn', code) ou ('timeout', None)."""
        end = time.time() + timeout
        while time.time() < end:
            b = self.read_byte()
            if b is None:
                continue
            if b == SEP:
                # Lire le code touche avec un court timeout (évite le blocage si SEP isolé)
                code = self.read_byte()
                t2 = time.time() + 0.5
                while code is None and time.time() < t2:
                    code = self.read_byte()
                if code is None:
                    continue          # SEP parasite → ignorer, ne pas bloquer
                return ('fn', code)
            return ('char', b)
        return ('timeout', None)


def wrap(text, width=COLS):
    out = []
    for para in text.split("\n"):
        if not para.strip():
            out.append("")
            continue
        cur = ""
        for word in para.split():
            if len(cur) + len(word) + (1 if cur else 0) <= width:
                cur = (cur + " " + word).strip()
            else:
                out.append(cur)
                cur = word[:width]
        if cur:
            out.append(cur)
    return out


# ── Écrans ───────────────────────────────────────────────────────────────
def show_home(t: Term, title_msg, question_msg):
    t.clear()
    t.w(bytes([CR, LF]))
    t.w(FG_CYAN)
    for ln in TITLE_LINES:
        t.center(ln)
    t.w(bytes([CR, LF, CR, LF]))      # 2 lignes après le logo
    t.w(FG_YELLOW)
    t.center(title_msg)
    t.w(bytes([CR, LF, CR, LF]))      # ligne vide après le message titre
    t.w(FG_WHITE)
    t.center(question_msg)
    t.w(bytes([CR, LF, CR, LF]))      # 1 ligne vide avant la saisie


def read_question(t: Term):
    """Lit une question. Retourne (texte, 'envoi') / (None,'sommaire') / (None,'timeout')."""
    t.w(FG_GREEN)
    t.w("> ")
    buf = []
    # Le Minitel fait l'écho local des frappes : on ne ré-écho PAS côté Pi.
    while True:
        kind, code = t.read_key(IDLE_TIMEOUT)
        if kind == 'timeout':
            return None, 'timeout'
        if kind == 'fn':
            if code == K_SOMMAIRE:
                return None, 'sommaire'
            if code == K_ENVOI:
                if buf:
                    return "".join(buf), 'envoi'
            if code in (K_CORR, K_RETOUR):
                if buf:
                    buf.pop()
                    t.w(bytes([BS, 0x20, BS]))   # backspace destructif
            continue
        # caractère
        c = code
        if c in (CR, LF):
            if buf:
                return "".join(buf), 'envoi'
        elif c in (BS, 0x7F):
            if buf:
                buf.pop()
                t.w(bytes([BS, 0x20, BS]))
        elif 0x20 <= c <= 0x7E:
            buf.append(chr(c))       # pas d'écho (le Minitel l'affiche)


def show_response(t: Term, text: str):
    """Affiche la réponse en pages. Retourne 'sommaire' / 'done' / 'timeout'."""
    lines = wrap(text)
    pages = [lines[i:i+CONTENT_ROWS] for i in range(0, len(lines), CONTENT_ROWS)] or [[""]]
    for pidx, page in enumerate(pages):
        t.clear()
        t.w(FG_WHITE)
        for ln in page:
            t.line(ln)
        last = (pidx == len(pages) - 1)
        if not last:
            t.w(bytes([CR, LF]))
            t.w(FG_CYAN)
            t.center("-- SUITE pour la suite --")
            while True:
                kind, code = t.read_key(IDLE_TIMEOUT)
                if kind == 'timeout':
                    return 'timeout'
                if kind == 'fn':
                    if code == K_SUITE:
                        break
                    if code == K_SOMMAIRE:
                        return 'sommaire'
    return 'done'


# ── Boucle principale ────────────────────────────────────────────────────
def run():
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    t = Term()
    log.info(f"Démarré sur {PORT} (modèle {MODEL})")

    while True:  # boucle sommaire
        # Recharger le preset à chaque retour au sommaire (prise en compte des édits)
        system_prompt, title_msg, question_msg, loading_msg = load_preset()
        history = []
        show_home(t, title_msg, question_msg)

        while True:  # boucle conversation
            question, action = read_question(t)
            if action in ('sommaire', 'timeout'):
                break

            history.append({"role": "user", "content": question})
            log.info(f"Q: {question!r}")

            t.w(bytes([CR, LF]))
            t.w(FG_CYAN); t.line(""); t.center(loading_msg)
            try:
                resp = client.messages.create(
                    model=MODEL, max_tokens=700,
                    system=system_prompt, messages=history,
                )
                answer = resp.content[0].text.strip()
                history.append({"role": "assistant", "content": answer})
                answer = answer.encode("ascii", errors="replace").decode("ascii")
            except Exception as e:
                log.error(f"API: {e}")
                answer = "Erreur de connexion. Reessayez."

            result = show_response(t, answer)
            if result in ('sommaire', 'timeout'):
                break

            # Invite pour rebondir
            t.w(bytes([CR, LF]))
            t.w(FG_WHITE)
            t.center("Repondez ou SOMMAIRE pour finir")
            t.w(bytes([CR, LF]))

            if len(history) > 20:
                history = history[-20:]


if __name__ == "__main__":
    run()
