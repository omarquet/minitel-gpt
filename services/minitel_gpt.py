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

# Dessins ASCII : {art}...{/art}. Sans protection, un dessin est detruit deux
# fois - strip_markdown mange les "_" et les "*" par paires, et wrap() fait un
# para.split() qui ecrase les espaces d'alignement et refusionne les lignes.
# Chaque ligne du bloc est donc prefixee par ART_MARK, un caractere de controle
# qui traverse to_ascii et apply_minitel_markup sans etre touche, et que wrap()
# reconnait pour recopier la ligne telle quelle.
ART_MARK = "\x01"
_ART_RE = re.compile(r"\{art\}[ \t]*\n?(.*?)\n?[ \t]*\{/art\}", re.S)

# Typographie francaise : une espace precede ? ! : et ;. wrap() decoupe sur les
# espaces, il peut donc laisser la ponctuation seule en debut de ligne
# ("...en 1989" / "? Eh bien..."). Le temps du decoupage, cette espace est
# remplacee par une sentinelle que split() ne considere pas comme un separateur
# (ce n'est pas un caractere blanc) ; elle redevient une espace a la sortie.
NBSP_MARK = "\x02"
_FR_PUNCT_RE = re.compile(r"[ \t]+([?!:;])")


def _mark_art_lines(block: str) -> str:
    return "\n".join(ART_MARK + ln for ln in block.split("\n"))


def strip_markdown(s: str) -> str:
    if not s:
        return s
    # Les blocs {art} sont mis de cote avant les substitutions Markdown.
    blocks = []

    def stash(m):
        blocks.append(m.group(1))
        return f"{ART_MARK}#{len(blocks) - 1}#"

    s = _ART_RE.sub(stash, s)
    for pattern, repl in _MARKDOWN_PATTERNS:
        s = pattern.sub(repl, s)
    for i, block in enumerate(blocks):
        s = s.replace(f"{ART_MARK}#{i}#", _mark_art_lines(block))
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
# Defaut aligne sur celui de l'admin (admin_ui.gemini_model) : le
# precedent, gemini-2.0-flash, a ete retire par Google et repond 404.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

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

    Le "prompt_file" des presets n'est PAS resolu ici : le copier dans
    prompts.json figerait le defaut du depot en override des la creation, et
    une installation neuve ne se comporterait pas comme une installation
    existante. La resolution se fait a chaque lecture, dans resolve_prompt()."""
    if PROMPTS_FILE.exists() or not PROMPTS_DEFAULT.exists():
        return
    PROMPTS_FILE.write_text(PROMPTS_DEFAULT.read_text(encoding="utf-8"),
                            encoding="utf-8")


def default_prompt_file(preset):
    """Chemin du fichier texte de prompt par defaut d'un preset, s'il existe."""
    name = (preset.get("prompt_file") or "").strip()
    if not name or "/" in name or "\\" in name:
        return None
    f = PROMPTS_TEXT_DIR / name
    return f if f.is_file() else None


def resolve_prompt(preset):
    """Prompt systeme effectif d'un preset, en deux couches :

    1. le champ "prompt" de prompts.json, s'il est renseigne - c'est l'override
       ecrit par l'admin web ;
    2. sinon le fichier texte designe par "prompt_file" (dans config/prompts/),
       qui est le defaut fourni par le depot.

    Le defaut reste donc vivant : tant que l'admin n'a rien saisi, modifier le
    .txt et redeployer suffit a changer la personnalite. Vider le champ dans
    prompts.json ramene au defaut.

    Un `.get("prompt", FALLBACK_PROMPT)` ne suffisait pas : la cle existe avec
    la valeur "" dans prompts.default.json, donc le defaut de .get() ne partait
    jamais et le LLM recevait un prompt vide, sans le moindre avertissement."""
    override = (preset.get("prompt") or "").strip()
    if override:
        return override
    f = default_prompt_file(preset)
    if f:
        return f.read_text(encoding="utf-8").strip()
    log.warning("Preset sans prompt ni prompt_file exploitable : "
                "repli sur FALLBACK_PROMPT")
    return FALLBACK_PROMPT


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
    choice = r.json()["choices"][0]
    # Une reponse coupee au plafond arrive sinon en silence : sur le Minitel
    # elle se lit comme une phrase qui s'arrete en plein mot, sans indice.
    if choice.get("finish_reason") == "length":
        log.warning("Reponse Mistral tronquee (plafond de 700 tokens atteint)")
    return choice["message"]["content"].strip()


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
    data = r.json()
    if data.get("stop_reason") == "max_tokens":
        log.warning("Reponse Claude tronquee (plafond de tokens atteint)")
    blocks = data.get("content", [])
    return "".join(b.get("text", "") for b in blocks
                   if b.get("type") == "text").strip()


# Modeles Gemini acceptant thinkingConfig, decouvert au premier appel : les
# variantes Lite le refusent avec un 400 alors qu'elles ne reflechissent pas.
_GEMINI_THINKING_PARAM = {}


def call_gemini(system_prompt, history):
    """Appelle l'API Gemini (generateContent) et retourne le texte de reponse.
    Le prompt systeme est passe a part (champ `systemInstruction`), pas dans
    `contents` - comme le champ `system` de Claude et le message `role: system`
    de Mistral."""
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_KEY/GEMINI_API_KEY non configure")

    contents = []
    for item in history:
        role = "user" if item.get("role") == "user" else "model"
        contents.append({
            "role": role,
            "parts": [{"text": item.get("content", "")}],
        })

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
    config = {"maxOutputTokens": 700}
    # Les tokens de "reflexion" des modeles recents sont decomptes du plafond de
    # sortie : gemini-3.5-flash en consommait 668 sur 700 et rendait une reponse
    # coupee en plein mot. Augmenter le plafond ne suffit pas, le modele
    # reflechit d'autant plus (1893 tokens sur 2000). On la coupe donc : sur des
    # reponses de 15 lignes de 40 colonnes, elle n'apporte rien.
    if _GEMINI_THINKING_PARAM.get(GEMINI_MODEL, True):
        config["thinkingConfig"] = {"thinkingBudget": 0}

    body = {"contents": contents, "generationConfig": config}
    if system_prompt:
        body["systemInstruction"] = {"parts": [{"text": system_prompt}]}

    r = requests.post(url, json=body, timeout=30)
    if r.status_code == 400 and "thinkingConfig" in config:
        # Certains modeles (les variantes Lite) refusent ce parametre. Ils ne
        # reflechissent pas de toute facon : on retient le refus pour ne pas
        # rejouer l'aller-retour a chaque question, et on repart sans.
        log.info("Gemini %s refuse thinkingConfig : appels suivants sans", GEMINI_MODEL)
        _GEMINI_THINKING_PARAM[GEMINI_MODEL] = False
        del config["thinkingConfig"]
        r = requests.post(url, json=body, timeout=30)
    r.raise_for_status()

    data = r.json()
    parts = []
    for candidate in data.get("candidates", []):
        if candidate.get("finishReason") == "MAX_TOKENS":
            log.warning("Reponse Gemini tronquee (plafond de %d tokens atteint)",
                        config["maxOutputTokens"])
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

# Un {grand} laisse ouvert divise par deux la largeur utile : 2 colonnes par
# caractere, donc ~19 caracteres par ligne au lieu de 39. Le texte part alors
# en lignes de trois mots, illisible (constate en production : une reponse
# entiere passee en double taille). Le prompt demande de s'en servir pour
# quelques mots et de toujours refermer, mais on ne peut pas plus compter
# sur un modele ici qu'on ne compte sur lui pour ne pas produire de Markdown
# (cf. strip_markdown). On borne donc le passage a une ligne d'ecran.
MARKUP_DOUBLE_MAX_COLS = COLS // 2 - 1        # 19 caracteres visibles
# Portee d'un {grand} : jusqu'a son {/}, un autre {grand}, ou la fin du texte.
_MARKUP_DOUBLE_RE = re.compile(r"\{grand\}(.*?)(?=\{/\}|\{grand\}|$)", re.S)


def bound_double_size(text, max_cols=MARKUP_DOUBLE_MAX_COLS):
    """Referme un {grand} dont le passage depasse une ligne d'ecran.

    Le texte n'est jamais modifie : seule la mise en forme est coupee, en
    inserant un {/} au dernier espace avant la limite (a defaut, pile sur la
    limite). Les autres balises du passage ne comptent pas comme du texte
    visible et sont laissees en place."""
    def repl(m):
        span = m.group(1)
        # Position dans `span` de chaque caractere visible, tags exclus.
        pos, i = [], 0
        while i < len(span):
            tag = _MINITEL_MARKUP_RE.match(span, i)
            if tag:
                i = tag.end()
                continue
            pos.append(i)
            i += 1
        if len(pos) <= max_cols:
            return m.group(0)
        cut = pos[max_cols]
        # cut+1 : si la limite tombe pile sur une espace, on coupe dessus
        # plutot que sur la precedente, ce qui sacrifiait un mot entier.
        espace = span.rfind(" ", 0, cut + 1)
        if espace > 0:
            cut = espace
        return "{grand}" + span[:cut] + "{/}" + span[cut:]
    return _MARKUP_DOUBLE_RE.sub(repl, text)


MARKUP_INSTRUCTIONS = (
    "\n\nMise en forme disponible (a utiliser avec parcimonie, pour "
    "souligner un mot ou une phrase clef, jamais pour tout le texte) : "
    "{rouge}...{/}, {vert}...{/}, {jaune}...{/}, {bleu}...{/}, "
    "{magenta}...{/}, {cyan}...{/}, {blanc}...{/} pour changer la couleur. "
    "Toujours refermer avec {/}. N'utilise RIEN d'autre comme mise en forme."
    "\n\nIl existe aussi {grand}...{/} (double hauteur et largeur), mais il "
    "coute cher : en double taille il ne tient plus que 20 caracteres par "
    "ligne au lieu de 40, et une phrase entiere en {grand} devient un texte "
    "hache en lignes de trois mots, illisible. Reserve-le donc a UN mot, "
    "trois au maximum, jamais plus d'une ligne, et referme-le "
    "immediatement. En cas de doute, utilise une couleur plutot que {grand}."
    "\n\nDessins ASCII : UNIQUEMENT si on te demande explicitement un dessin, "
    "un logo ou un schema, encadre-le par {art} et {/art}, chacun seul sur sa "
    "ligne. Les lignes entre les deux sont affichees telles quelles, espaces "
    "compris, sans reformatage : 39 colonnes de large et 15 lignes de haut au "
    "maximum, caracteres ASCII simples uniquement, aucune autre mise en forme "
    "a l'interieur. Pour toute autre question, n'utilise jamais {art}."
    "\n\nDans tous les cas, reponds directement. N'ecris jamais ton "
    "raisonnement, ne verifie rien a voix haute, ne commente pas tes "
    "contraintes d'affichage ni le nombre de colonnes, et reponds toujours "
    "dans la langue de la question."
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
    return _MINITEL_MARKUP_RE.sub(repl, bound_double_size(text))

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
        prompt = resolve_prompt(p)
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
            render_logo(p),
        )
    except Exception as e:
        log.warning(f"prompts.json: {e}")
        return (FALLBACK_PROMPT, "*** MINITEL GPT ***",
                "Posez votre question :", "Consultation en cours...",
                TITLE_LINES)


# ── ASCII title (pyfiglet) ───────────────────────────────────────────────
# Un logo ne doit jamais remplir les 40 colonnes : une ligne pleine fait deja
# passer le curseur du Minitel a la rangee suivante, et le CR LF ajoute par
# line() y laisserait une ligne vide. On s'arrete donc a 39, et on plafonne la
# hauteur pour que le logo, les deux messages et la ligne de saisie tiennent
# dans les 24 rangees de l'ecran.
LOGO_MAX_COLS = COLS - 1
LOGO_MAX_LINES = 12


def figlet_lines(word, font):
    """Rend un mot en lettres ASCII. Retourne [] si la police est inconnue ou
    si pyfiglet est absent, pour que l'appelant puisse se rabattre."""
    try:
        from pyfiglet import Figlet
        rendu = Figlet(font=font, width=COLS).renderText(word).rstrip("\n")
        lines = [ln[:LOGO_MAX_COLS] for ln in rendu.split("\n") if ln.strip()]
        return lines[:LOGO_MAX_LINES]
    except Exception as e:
        log.warning("pyfiglet (%s / %s): %s", word, font, e)
        return []


def build_title():
    lines = figlet_lines("MINITEL", "small") + figlet_lines("GPT", "standard")
    return lines or ["", "    M I N I T E L   G P T", ""]


TITLE_LINES = build_title()


def render_logo(preset):
    """Lignes du logo d'un preset, par ordre de priorite : le dessin libre
    (logo_art) s'il est renseigne, sinon un mot rendu par pyfiglet
    (logo_text / logo_font), sinon le logo MINITEL GPT par defaut."""
    art = (preset.get("logo_art") or "").strip("\n")
    if art.strip():
        return [ln[:LOGO_MAX_COLS] for ln in art.split("\n")][:LOGO_MAX_LINES]
    # Le mot est passe tel quel a pyfiglet, espaces de bord compris : ils
    # decalent le rendu et permettent d'ajuster le centrage. Seul un champ
    # entierement blanc compte comme vide.
    word = preset.get("logo_text") or ""
    if word.strip():
        lines = figlet_lines(word, (preset.get("logo_font") or "small").strip())
        if lines:
            return lines
    return TITLE_LINES


# ── Serial helpers ───────────────────────────────────────────────────────
# Sur le vrai Minitel, un caractere en double largeur occupe 2 des 40
# colonnes (pas 1) : ESC+0x4E (double largeur) et 0x4F (double grandeur)
# passent la largeur de colonne a 2 ; 0x4C (normal) et 0x4D (double
# hauteur seule, ne touche pas la largeur) la remettent/laissent a 1.
_COLUMN_WIDTH_BY_SIZE_BYTE = {0x4C: 1, 0x4D: 1, 0x4E: 2, 0x4F: 2}


def visible_len(s, start_width=1):
    """Longueur affichee en colonnes : les sequences ESC+octet (couleur/
    taille) ont une largeur nulle, et un caractere en double largeur
    compte pour 2 colonnes tant que le mode n'est pas remis a normal.
    `start_width` permet de tenir compte d'un mode double deja actif
    avant `s` (ex. {grand} ouvert sur une ligne precedente)."""
    n, i, col_width = 0, 0, start_width
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


def _col_width_after(s, start_width=1):
    """Etat de largeur de colonne (1 ou 2) juste apres `s`, en partant de
    `start_width` (pour propager le mode double d'une ligne a l'autre)."""
    i, col_width = 0, start_width
    while i < len(s):
        if s[i] == chr(ESC) and i + 1 < len(s):
            b = ord(s[i + 1])
            if b in _COLUMN_WIDTH_BY_SIZE_BYTE:
                col_width = _COLUMN_WIDTH_BY_SIZE_BYTE[b]
            i += 2
            continue
        i += 1
    return col_width


def visible_truncate(s, width, start_width=1):
    """Tronque a `width` COLONNES affichees (double largeur = 2 colonnes),
    en preservant les sequences ESC+octet rencontrees en cours de route
    (jamais coupees en deux). `start_width` : voir `visible_len`."""
    out, n, i, col_width = [], 0, 0, start_width
    while i < len(s):
        if s[i] == chr(ESC) and i + 1 < len(s):
            b = ord(s[i + 1])
            if b in _COLUMN_WIDTH_BY_SIZE_BYTE:
                col_width = _COLUMN_WIDTH_BY_SIZE_BYTE[b]
            out.append(s[i:i + 2]); i += 2
            continue
        if n + col_width > width:
            break
        out.append(s[i]); n += col_width; i += 1
    return "".join(out)


def wrap(text, width=COLS):
    # Une ligne qui remplit exactement les 40 colonnes fait DEJA passer le
    # curseur du Minitel a la rangee suivante (debordement automatique en fin
    # de rangee). Le CR LF emis ensuite par line() descend alors une seconde
    # fois et laisse une ligne vide a l'ecran, de facon apparemment aleatoire
    # puisque cela ne touche que les lignes pleines. On s'arrete donc une
    # colonne avant. Vaut aussi pour les blocs {art}, tronques plus bas.
    width = min(width, COLS - 1)
    out = []
    # Etat (1 ou 2 colonnes/caractere) qui persiste d'une ligne a l'autre :
    # un {grand} ouvert sur une ligne et referme plus loin doit continuer a
    # compter double sur les lignes suivantes, sinon on sous-estime leur
    # largeur reelle et le texte deborde des 40 colonnes a l'affichage.
    col_width = 1
    for para in text.split("\n"):
        # Ligne de dessin ({art}) : recopiee telle quelle, espaces d'alignement
        # compris. Seule concession, la troncature a la largeur de l'ecran.
        # Un dessin est toujours en taille normale : on remet col_width a 1.
        if para.startswith(ART_MARK):
            col_width = 1
            out.append(visible_truncate(para[len(ART_MARK):], width))
            continue
        if not para.strip():
            out.append("")
            continue
        para = _FR_PUNCT_RE.sub(NBSP_MARK + r"\1", para)
        cur = ""
        line_width = col_width   # etat au debut de la ligne en construction
        for word in para.split():
            # Mesure la ligne candidate d'un seul tenant (pas cur et word
            # separement) pour que l'etat couleur/taille se propage bien
            # d'un mot au suivant (ex. {grand}mot1 mot2{/} sur 2 mots).
            candidate = (cur + " " + word).strip() if cur else word
            if visible_len(candidate, line_width) <= width:
                cur = candidate
                col_width = _col_width_after(candidate, line_width)
            else:
                out.append(cur)
                line_width = col_width
                cur = visible_truncate(word, width, line_width)
                col_width = _col_width_after(cur, line_width)
        if cur:
            out.append(cur)
    # La sentinelle redevient une espace ordinaire une fois le decoupage fait.
    return [ln.replace(NBSP_MARK, " ") for ln in out]


# ── Écrans ───────────────────────────────────────────────────────────────
def show_home(t, title_msg, question_msg, title_lines=None):
    t.clear()
    t.w(bytes([CR, LF]))
    t.w(FG_CYAN)
    for ln in (TITLE_LINES if title_lines is None else title_lines):
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
    (None,'repetition') / (None,'retour') / (None,'timeout').
    RETOUR efface le dernier caractère s'il y en a un, sinon (saisie vide)
    il demande a revoir la derniere reponse depuis sa derniere page."""
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
            if code == K_CORR:
                if buf:
                    buf.pop()
                    t.w(bytes([BS, 0x20, BS]))   # backspace destructif
            if code == K_RETOUR:
                if buf:
                    buf.pop()
                    t.w(bytes([BS, 0x20, BS]))   # backspace destructif
                else:
                    return None, 'retour'
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


def show_response(t, text: str, start_at_last=False):
    """Affiche la réponse en pages. RETOUR revient sur une page precedente
    (autant de fois que necessaire), SUITE avance, SOMMAIRE abandonne.
    Retourne 'sommaire' / 'done' / 'timeout'.

    start_at_last=True (revision apres coup, cf. l'action 'retour' de
    read_question) : demarre sur l'avant-derniere page (RETOUR doit
    montrer autre chose que ce qu'on vient deja de lire, pas la repeter)
    et attend une touche meme sur la derniere page, contrairement au mode
    normal qui rend la main directement (SUITE y vaut alors "terminer la
    revision")."""
    lines = wrap(text)
    pages = [lines[i:i+CONTENT_ROWS] for i in range(0, len(lines), CONTENT_ROWS)] or [[""]]
    pidx = max(0, len(pages) - 2) if start_at_last else 0
    while True:
        t.clear()
        t.w(FG_WHITE)
        for ln in pages[pidx]:
            t.line(ln)
        last = (pidx == len(pages) - 1)
        if last and not start_at_last:
            return 'done'
        t.w(bytes([CR, LF]))
        t.w(SZ_NORMAL + FG_CYAN)
        t.center("-- SUITE pour continuer --" if last else "-- SUITE pour la suite --")
        if pidx > 0:
            t.center("-- RETOUR pour la page precedente --")
        while True:
            kind, code = t.read_key(IDLE_TIMEOUT)
            if kind == 'timeout':
                return 'timeout'
            if kind == 'fn':
                if code == K_SUITE:
                    if last:
                        return 'done'
                    pidx += 1
                    break
                if code == K_SOMMAIRE:
                    return 'sommaire'
                if code == K_RETOUR and pidx > 0:
                    pidx -= 1
                    break


