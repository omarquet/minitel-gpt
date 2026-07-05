#!/usr/bin/env python3
"""
MINITEL GPT - service de chat années 80 sur Minitel.
Interface : sommaire (titre ASCII + invite) → saisie → réponse paginée → re-saisie.
Touches : ENVOI = valider, SUITE = page suivante, SOMMAIRE = retour accueil.
Timeout 5 min sans action → retour sommaire.
"""
import json
import os
import re
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

# Le Minitel ne sait pas afficher le Markdown (pas de gras/italique/titres) :
# le prompt systeme demande au LLM de ne pas en generer (voir load_preset),
# mais on retire quand meme la syntaxe au cas ou, plutot que d'afficher les
# symboles bruts (**, _, #, `) a l'ecran.
_MARKDOWN_PATTERNS = [
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),              # **gras**
    (re.compile(r"__(.+?)__"), r"\1"),                  # __gras__
    (re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)"), r"\1"),  # *italique*
    (re.compile(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)"), r"\1"),        # _italique_
    (re.compile(r"`([^`]+)`"), r"\1"),                  # `code`
    (re.compile(r"^#{1,6}\s+", re.M), ""),              # # Titre
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),      # [texte](url)
]

def strip_markdown(s: str) -> str:
    if not s:
        return s
    for pattern, repl in _MARKDOWN_PATTERNS:
        s = pattern.sub(repl, s)
    return s

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


PROMPTS_TEXT_DIR = Path(__file__).parent.parent / "config" / "prompts"


def ensure_prompts():
    """prompts.json est local (gitignoré) : si absent (1er lancement / après une
    mise à jour), on le crée depuis prompts.default.json fourni par le dépôt.

    Un preset peut référencer son prompt via "prompt_file" (nom de fichier
    dans config/prompts/) plutôt qu'une chaîne JSON échappée sur une seule
    ligne - plus simple à éditer/relire. Résolu une seule fois ici, à la
    création : prompts.json reste ensuite un JSON autonome, éditable
    normalement depuis l'admin web (le champ "prompt" est alors la source)."""
    if PROMPTS_FILE.exists() or not PROMPTS_DEFAULT.exists():
        return
    data = json.loads(PROMPTS_DEFAULT.read_text(encoding="utf-8"))
    for preset in data.get("presets", {}).values():
        prompt_file = preset.get("prompt_file")
        if not prompt_file:
            continue
        f = PROMPTS_TEXT_DIR / prompt_file
        if f.exists():
            preset["prompt"] = f.read_text(encoding="utf-8").strip()
    PROMPTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
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
# Couleurs de texte (ESC + 0x40-0x47, norme Videotex/Teletel).
FG_BLACK   = bytes([ESC,0x40])
FG_RED     = bytes([ESC,0x41])
FG_GREEN   = bytes([ESC,0x42])
FG_YELLOW  = bytes([ESC,0x43])
FG_BLUE    = bytes([ESC,0x44])
FG_MAGENTA = bytes([ESC,0x45])
FG_CYAN    = bytes([ESC,0x46])
FG_WHITE   = bytes([ESC,0x47])
BG_BLACK   = bytes([ESC,0x50])
# Taille de caractere (ESC + 0x4C-0x4F). SZ_NORMAL et DBL_HEIGHT etaient
# mal etiquetes avant verification aupres de la norme (0x4C = taille
# normale, pas double hauteur).
SZ_NORMAL  = bytes([ESC,0x4C])   # taille normale
DBL_HEIGHT = bytes([ESC,0x4D])   # double hauteur
DBL_WIDTH  = bytes([ESC,0x4E])   # double largeur
DBL_SIZE   = bytes([ESC,0x4F])   # double hauteur+largeur

# Mise en forme legere pour les reponses du LLM : convention a nous (pas un
# standard), traduite en vrais codes Videotex. {/} reinitialise couleur et
# taille. Voir MARKUP_INSTRUCTIONS pour la consigne donnee au LLM.
MINITEL_MARKUP_TAGS = {
    "rouge": FG_RED, "vert": FG_GREEN, "jaune": FG_YELLOW, "bleu": FG_BLUE,
    "magenta": FG_MAGENTA, "cyan": FG_CYAN, "blanc": FG_WHITE,
    "grand": DBL_SIZE,
}
MINITEL_MARKUP_RESET = FG_WHITE + SZ_NORMAL
_MINITEL_MARKUP_RE = re.compile(r"\{(/|[a-z]+)\}")

MARKUP_INSTRUCTIONS = (
    "\n\nMise en forme disponible (a utiliser avec parcimonie, pour "
    "souligner un mot ou une phrase clef, jamais pour tout le texte) : "
    "{rouge}...{/}, {vert}...{/}, {jaune}...{/}, {bleu}...{/}, "
    "{magenta}...{/}, {cyan}...{/}, {blanc}...{/} pour changer la couleur, "
    "{grand}...{/} pour un texte en double hauteur/largeur. "
    "Toujours refermer avec {/}. N'utilise RIEN d'autre comme mise en forme."
)


def apply_minitel_markup(text):
    """Traduit {rouge}...{/}, {grand}...{/} etc. en codes Videotex reels."""
    if not text:
        return text
    def repl(m):
        tag = m.group(1)
        if tag == "/":
            return MINITEL_MARKUP_RESET.decode("latin1")
        code = MINITEL_MARKUP_TAGS.get(tag)
        return code.decode("latin1") if code else ""
    return _MINITEL_MARKUP_RE.sub(repl, text)

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
    """Retourne (prompt, title_msg, question_msg, loading_msg).
    Le prompt inclut les fichiers de connaissance du preset s'il y en a."""
    try:
        ensure_prompts()
        data = json.load(open(PROMPTS_FILE))
        key = data["active"]
        p = data["presets"][key]
        prompt = p.get("prompt", FALLBACK_PROMPT)
        knowledge = load_knowledge(key)
        if knowledge:
            prompt += ("\n\nCONNAISSANCES DE REFERENCE (utilise ces informations "
                       "en priorite pour repondre) :\n" + knowledge)
        prompt += ("\n\nContrainte technique absolue : tu t'affiches sur un ecran "
                   "Minitel qui ne sait pas afficher le Markdown. N'utilise "
                   "JAMAIS de syntaxe Markdown (pas de **gras**, *italique*, "
                   "# titres, listes a puces avec * ou -, blocs de code avec "
                   "des accents graves, liens [texte](url))." + MARKUP_INSTRUCTIONS)
        return (
            prompt,
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
# Sur le vrai Minitel, un caractere en double largeur occupe 2 des 40
# colonnes (pas 1) : ESC+0x4E (double largeur) et 0x4F (double grandeur)
# passent la largeur de colonne a 2 ; 0x4C (normal) et 0x4D (double
# hauteur seule, ne touche pas la largeur) la remettent/laissent a 1.
_COLUMN_WIDTH_BY_SIZE_BYTE = {0x4C: 1, 0x4D: 1, 0x4E: 2, 0x4F: 2}


def visible_len(s):
    """Longueur affichee en colonnes : les sequences ESC+octet (couleur/
    taille) ont une largeur nulle, et un caractere en double largeur
    compte pour 2 colonnes tant que le mode n'est pas remis a normal."""
    n, i, col_width = 0, 0, 1
    while i < len(s):
        if s[i] == chr(ESC) and i + 1 < len(s):
            b = ord(s[i + 1])
            if b in _COLUMN_WIDTH_BY_SIZE_BYTE:
                col_width = _COLUMN_WIDTH_BY_SIZE_BYTE[b]
            i += 2
            continue
        n += col_width
        i += 1
    return n


def visible_truncate(s, width):
    """Tronque a `width` COLONNES affichees (double largeur = 2 colonnes),
    en preservant les sequences ESC+octet rencontrees en cours de route
    (jamais coupees en deux)."""
    out, n, i, col_width = [], 0, 0, 1
    while i < len(s) and n < width:
        if s[i] == chr(ESC) and i + 1 < len(s):
            b = ord(s[i + 1])
            if b in _COLUMN_WIDTH_BY_SIZE_BYTE:
                col_width = _COLUMN_WIDTH_BY_SIZE_BYTE[b]
            out.append(s[i:i + 2]); i += 2
            continue
        out.append(s[i]); n += col_width; i += 1
    return "".join(out)


def wrap(text, width=COLS):
    out = []
    for para in text.split("\n"):
        if not para.strip():
            out.append("")
            continue
        cur = ""
        for word in para.split():
            # Mesure la ligne candidate d'un seul tenant (pas cur et word
            # separement) pour que l'etat couleur/taille se propage bien
            # d'un mot au suivant (ex. {grand}mot1 mot2{/} sur 2 mots).
            candidate = (cur + " " + word).strip() if cur else word
            if visible_len(candidate) <= width:
                cur = candidate
            else:
                out.append(cur)
                cur = visible_truncate(word, width)
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
    """Affiche la réponse en pages. RETOUR revient sur une page precedente
    (autant de fois que necessaire), SUITE avance, SOMMAIRE abandonne.
    Retourne 'sommaire' / 'done' / 'timeout'."""
    lines = wrap(text)
    pages = [lines[i:i+CONTENT_ROWS] for i in range(0, len(lines), CONTENT_ROWS)] or [[""]]
    pidx = 0
    while True:
        t.clear()
        t.w(FG_WHITE)
        for ln in pages[pidx]:
            t.line(ln)
        if pidx == len(pages) - 1:
            return 'done'
        t.w(bytes([CR, LF]))
        t.w(FG_CYAN)
        t.center("-- SUITE pour la suite --")
        if pidx > 0:
            t.center("-- RETOUR pour la page precedente --")
        while True:
            kind, code = t.read_key(IDLE_TIMEOUT)
            if kind == 'timeout':
                return 'timeout'
            if kind == 'fn':
                if code == K_SUITE:
                    pidx += 1
                    break
                if code == K_SOMMAIRE:
                    return 'sommaire'
                if code == K_RETOUR and pidx > 0:
                    pidx -= 1
                    break


