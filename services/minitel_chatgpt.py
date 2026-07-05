#!/usr/bin/env python3
"""
MINITEL GPT - service de chat années 80 sur Minitel.
Interface : sommaire (titre ASCII + invite) → saisie → réponse paginée → re-saisie.
Touches : ENVOI = valider, SUITE = page suivante, SOMMAIRE = retour accueil.
Timeout 5 min sans action → retour sommaire.
"""
import json
import os
import sys
import logging
import unicodedata
from pathlib import Path
from dotenv import load_dotenv
import requests

# Translittération vers ASCII affichable sur Minitel (é→e, œ→oe, …) :
# évite les « ? » que produisait encode('ascii','replace').
_ASCII_REPL = {
    "œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "€": "EUR",
    "’": "'", "‘": "'", "“": '"', "”": '"', "«": '"', "»": '"',
    "–": "-", "-": "-", "…": "...", " ": " ", "·": ".", "•": "-",
}

def to_ascii(s: str) -> str:
    if not s:
        return s
    for k, v in _ASCII_REPL.items():
        s = s.replace(k, v)
    s = unicodedata.normalize("NFKD", s)
    return s.encode("ascii", "ignore").decode("ascii")

load_dotenv(Path(__file__).parent.parent / ".env")

# ── Config ───────────────────────────────────────────────────────────────
COLS = 40
SCREEN_ROWS = 24
CONTENT_ROWS = 18          # lignes de contenu par page de réponse
IDLE_TIMEOUT = 300         # 5 min → retour sommaire

# ── Fournisseur d'IA (LLM) ───────────────────────────────────────────────
# LLM_PROVIDER = "mistral" (defaut), "claude" ou "gemini". La cle et le
# modele de chaque fournisseur sont independants ; on bascule sans perdre
# l'autre configuration.
PROVIDER = os.getenv("LLM_PROVIDER", "mistral").strip().lower()

MISTRAL_KEY = os.environ.get("MISTRAL_KEY", "")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"

# Claude (Anthropic) - appele en HTTP brut comme Mistral, sans SDK.
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# Gemini - idem, HTTP brut.
GEMINI_KEY = os.getenv("GEMINI_KEY") or os.getenv("GEMINI_API_KEY") or ""
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# Modele effectivement utilise (pour les logs)
if PROVIDER == "claude":
    MODEL = CLAUDE_MODEL
elif PROVIDER == "gemini":
    MODEL = GEMINI_MODEL
else:
    MODEL = MISTRAL_MODEL
PROMPTS_FILE = Path(__file__).parent.parent / "config" / "prompts.json"
PROMPTS_DEFAULT = Path(__file__).parent.parent / "config" / "prompts.default.json"


def ensure_prompts():
    """prompts.json est local (gitignoré) : si absent (1er lancement / après une
    mise à jour), on le crée depuis prompts.default.json fourni par le dépôt."""
    if not PROMPTS_FILE.exists() and PROMPTS_DEFAULT.exists():
        PROMPTS_FILE.write_text(PROMPTS_DEFAULT.read_text(encoding="utf-8"),
                                encoding="utf-8")


def call_mistral(system_prompt, history):
    """Appelle l'API Mistral (chat completions) et retourne le texte de réponse."""
    messages = [{"role": "system", "content": system_prompt}] + history
    r = requests.post(
        MISTRAL_URL,
        headers={"Authorization": f"Bearer {MISTRAL_KEY}",
                 "Content-Type": "application/json"},
        json={"model": MISTRAL_MODEL, "messages": messages, "max_tokens": 700},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def call_claude(system_prompt, history):
    """Appelle l'API Claude (Anthropic Messages) et retourne le texte de réponse.
    Le prompt systeme est passe a part (champ `system`), pas dans `messages`."""
    r = requests.post(
        ANTHROPIC_URL,
        headers={"x-api-key": ANTHROPIC_KEY,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": CLAUDE_MODEL, "max_tokens": 700,
              "system": system_prompt, "messages": history},
        timeout=30,
    )
    r.raise_for_status()
    blocks = r.json().get("content", [])
    return "".join(b.get("text", "") for b in blocks
                   if b.get("type") == "text").strip()


def call_gemini(system_prompt, history):
    """Appelle l'API Gemini (generateContent) et retourne le texte de reponse."""
    if not GEMINI_KEY:
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

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
    r = requests.post(
        url,
        json={"contents": contents, "generationConfig": {"maxOutputTokens": 700}},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    parts = []
    for candidate in data.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            text = part.get("text", "") if isinstance(part, dict) else ""
            if text:
                parts.append(text)
    if parts:
        return "".join(parts).strip()
    raise RuntimeError(f"Reponse Gemini non exploitable: {data}")


def call_llm(system_prompt, history):
    """Aiguille vers le fournisseur configure (LLM_PROVIDER)."""
    if PROVIDER == "claude":
        return call_claude(system_prompt, history)
    if PROVIDER == "gemini":
        return call_gemini(system_prompt, history)
    return call_mistral(system_prompt, history)


# Journalisation : toujours sur la sortie standard (capturée par systemd/journald).
# Le fichier de log est un bonus : s'il n'est pas accessible (droits, FS plein…),
# on continue sans lui plutôt que de tuer le terminal. Un simple souci de log ne
# doit jamais empêcher l'affichage sur le Minitel.
_handlers = [logging.StreamHandler(sys.stdout)]
_LOG_FILE = Path(__file__).parent.parent / "logs" / "chatgpt.log"
try:
    _handlers.insert(0, logging.FileHandler(_LOG_FILE))
except Exception as _e:  # PermissionError, FileNotFoundError…
    print(f"[minitel-gpt] log fichier indisponible ({_e}), sortie standard seule",
          file=sys.stderr)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [minitel-gpt] %(levelname)s %(message)s",
    handlers=_handlers,
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

# Minitel 2 en mode péri-informatique : les touches de fonction sont émises
# en VT100 (SS3 = "ESC O x") au lieu du Videotex "SEP + code". Mapping x → code.
SS3_MAP = {0x4D: K_ENVOI, 0x50: K_SOMMAIRE, 0x6E: K_SUITE, 0x6D: K_GUIDE,
           0x52: K_RETOUR, 0x6C: K_CORR, 0x51: K_ANNUL}

FALLBACK_PROMPT = (
    "Tu es MINITEL GPT. Reponds en francais, concis (max 30 lignes de 40 caracteres), "
    "ASCII sans accents ni emojis. Ne mentionne jamais que tu es une autre IA."
)


KNOWLEDGE_DIR = Path(__file__).parent.parent / "config" / "knowledge"
KNOWLEDGE_MAX_CHARS = 12000   # plafond du contexte injecté (coût/latence)


def load_knowledge(active_key):
    """Concatène les fichiers .txt de connaissance du preset (plafonné)."""
    folder = KNOWLEDGE_DIR / active_key
    if not folder.is_dir():
        return ""
    parts = []
    total = 0
    for f in sorted(folder.glob("*.txt")):
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            continue
        if not txt:
            continue
        parts.append(f"--- {f.name} ---\n{txt}")
        total += len(txt)
        if total >= KNOWLEDGE_MAX_CHARS:
            break
    blob = "\n\n".join(parts)
    return blob[:KNOWLEDGE_MAX_CHARS]


def load_preset():
    """Retourne (system, title_msg, question_msg, loading_msg).
    Le system inclut les fichiers de connaissance du preset s'il y en a."""
    try:
        ensure_prompts()
        data = json.load(open(PROMPTS_FILE))
        key = data["active"]
        p = data["presets"][key]
        system = p.get("system", FALLBACK_PROMPT)
        knowledge = load_knowledge(key)
        if knowledge:
            system += ("\n\nCONNAISSANCES DE REFERENCE (utilise ces informations "
                       "en priorite pour repondre) :\n" + knowledge)
        return (
            system,
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
def show_home(t, title_msg, question_msg):
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


def read_question(t):
    """Lit une question. Retourne (texte, 'envoi') / (None,'sommaire') / (None,'guide') /
    (None,'repetition') / (None,'timeout')."""
    t.w(FG_GREEN)
    t.w("> ")
    buf = []
    # Le Minitel fait l'écho local des frappes : on ne ré-écho PAS côté serveur.
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
                    t.w(bytes([BS, 0x20, BS]))   # backspace destructif
            if code == K_ANNUL:
                if buf:
                    t.w(bytes([BS, 0x20, BS]) * len(buf))
                    buf.clear()
            if code == K_REPET:
                return None, 'repetition'
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


def show_response(t, text: str):
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


