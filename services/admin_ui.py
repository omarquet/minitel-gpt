#!/usr/bin/env python3
"""
Interface d'administration MINITEL GPT — http://<ip>:8080  (mot de passe 13100)
Sections : Personnalité (presets), Génération IA, Paramètres (clé/email), Services.
"""
import json
import os
import re
import subprocess
from pathlib import Path
from functools import wraps
from flask import (Flask, render_template_string, request, redirect,
                   url_for, session, jsonify, send_from_directory)

PROJ_DIR = Path(__file__).parent.parent
ASSETS_DIR = PROJ_DIR / "assets"
PROMPTS_FILE = PROJ_DIR / "config" / "prompts.json"
ENV_FILE = PROJ_DIR / ".env"
LOGS_DIR = PROJ_DIR / "logs"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "13100")
SECRET_KEY = os.getenv("FLASK_SECRET", "minitel-secret-1985")

DEFAULTS = {
    "title_msg": "*** MINITEL GPT ***",
    "question_msg": "Posez votre question :",
    "loading_msg": "Consultation en cours...",
}

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ── Helpers prompts ──────────────────────────────────────────────────────
def load_prompts():
    with open(PROMPTS_FILE) as f:
        return json.load(f)

def save_prompts(data):
    with open(PROMPTS_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def normalized_presets(data):
    """Renvoie les presets avec tous les champs (défauts comblés)."""
    out = {}
    for k, p in data["presets"].items():
        merged = dict(DEFAULTS)
        merged.update(p)
        merged.setdefault("system", "")
        merged.setdefault("label", k)
        out[k] = merged
    return out

# ── Helpers .env ─────────────────────────────────────────────────────────
def read_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

def write_env_key(key, value):
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n")

def anthropic_key():
    return read_env().get("ANTHROPIC_KEY", os.getenv("ANTHROPIC_KEY", ""))

def claude_model():
    return read_env().get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# ── Auth ─────────────────────────────────────────────────────────────────
def require_login(f):
    @wraps(f)
    def d(*a, **k):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*a, **k)
    return d

# ── Services ─────────────────────────────────────────────────────────────
def restart_terminal():
    r = subprocess.run(["sudo", "systemctl", "restart", "minitel-chatgpt"],
                       capture_output=True, text=True)
    return r.returncode == 0

def svc_status(name):
    return subprocess.run(["systemctl", "is-active", name],
                          capture_output=True, text=True).stdout.strip()

def log_tail(name, n=30):
    f = LOGS_DIR / f"{name}.log"
    if not f.exists():
        return "(pas de log)"
    try:
        return subprocess.run(["tail", f"-{n}", str(f)],
                              capture_output=True, text=True).stdout
    except Exception as e:
        return str(e)

# ── Génération de prompt par IA ──────────────────────────────────────────
def generate_prompt(description):
    import anthropic
    key = anthropic_key()
    if not key:
        raise RuntimeError("Clé Anthropic absente")
    client = anthropic.Anthropic(api_key=key)
    meta = (
        "Tu es expert en conception de prompts systeme pour un chatbot affiche "
        "sur un terminal Minitel (40 colonnes, ASCII sans accents ni emojis).\n\n"
        "A partir de la description ci-dessous, redige un prompt systeme complet "
        "en francais qui :\n"
        "1. Definit clairement le role, la personnalite et le ton du chatbot.\n"
        "2. Borne STRICTEMENT le chatbot au theme decrit (il refuse tout hors-sujet).\n"
        "3. Resiste aux tentatives de contournement, jailbreak ou changement de role.\n"
        "4. Impose des reponses concises (max 15 lignes de 40 caracteres), en ASCII "
        "sans accents.\n"
        "5. Donne 2-3 exemples de comportement attendu.\n\n"
        "Reponds UNIQUEMENT avec le texte du prompt systeme, sans preambule ni "
        "commentaire.\n\n"
        f"DESCRIPTION DU PROJET :\n{description}"
    )
    resp = client.messages.create(
        model=claude_model(), max_tokens=1500,
        messages=[{"role": "user", "content": meta}],
    )
    return resp.content[0].text.strip()

# ── Templates ────────────────────────────────────────────────────────────
LOGIN_HTML = """<!DOCTYPE html><html lang=fr><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>MinitelGPT Admin</title>
<link rel=icon type=image/png sizes=32x32 href=/assets/favicon-32.png>
<link rel=apple-touch-icon href=/assets/apple-touch-icon-180.png>
<style>body{background:#0d0d1a;color:#e0e0e0;font-family:'Courier New',monospace;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{background:#16202e;border:2px solid #4ecdc4;border-radius:12px;padding:40px;width:340px;text-align:center}
.box img{width:200px;height:auto;margin-bottom:12px}
input{width:100%;padding:12px;background:#0d0d1a;border:1px solid #4ecdc4;color:#e0e0e0;border-radius:6px;text-align:center;letter-spacing:.2em;box-sizing:border-box;font-size:16px}
button{width:100%;margin-top:12px;padding:12px;background:#4ecdc4;color:#0d1a1a;border:none;border-radius:6px;font-weight:bold;cursor:pointer;font-size:1em}
button:hover{background:#3db5ab}.err{color:#ff5b5b;margin-top:10px}</style></head><body>
<div class=box><img src=/assets/logo-minitel-gpt.png alt="MINITEL GPT">
<form method=POST><input type=password name=password placeholder="••••••" autofocus>
<button>Entrer</button></form>{% if error %}<div class=err>{{error}}</div>{% endif %}</div></body></html>"""

ADMIN_HTML = """<!DOCTYPE html><html lang=fr><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>MinitelGPT Admin</title>
<link rel=icon type=image/png sizes=32x32 href=/assets/favicon-32.png>
<link rel=apple-touch-icon href=/assets/apple-touch-icon-180.png>
<style>
:root{--accent:#4ecdc4;--accent-d:#3db5ab;--bg:#0d0d1a;--card:#16202e;--border:#234;--danger:#ff5b5b}
*{box-sizing:border-box}
body{background:var(--bg);color:#e0e0e0;font-family:'Courier New',monospace;margin:0;padding:0 0 40px}
.topbar{display:flex;justify-content:flex-end;padding:10px 20px}
.logout{color:#778;text-decoration:none;font-size:.85em}.logout:hover{color:var(--danger)}
.logo-top{text-align:center;padding:4px 0 18px}
.logo-top img{width:230px;max-width:70%;height:auto}
main{max-width:920px;margin:0 auto;padding:0 20px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:640px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:18px;margin-bottom:16px}
.card h2{color:var(--accent);margin:0 0 14px;font-size:1.05em}
.sub{color:#778;font-size:.8em;margin:-8px 0 12px}
label{color:#9ab;font-size:.85em;display:block;margin:10px 0 4px}
select,input[type=text],input[type=password],textarea{width:100%;padding:10px;background:var(--bg);
  border:1px solid var(--border);color:#e0e0e0;border-radius:6px;font-family:monospace;font-size:15px}
select:focus,input:focus,textarea:focus{outline:none;border-color:var(--accent)}
textarea{resize:vertical;font-size:.85em;line-height:1.5}
.btn{padding:10px 18px;border:none;border-radius:6px;cursor:pointer;font-weight:bold;font-size:.9em;font-family:monospace;margin:8px 8px 0 0}
.btn-p{background:var(--accent);color:#0d1a1a}.btn-p:hover{background:var(--accent-d)}
.btn-s{background:transparent;color:var(--accent);border:1px solid var(--accent)}.btn-s:hover{background:#16302e}
.btn-d{background:transparent;color:var(--danger);border:1px solid var(--danger)}.btn-d:hover{background:#2a1414}
.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.toolbar select{flex:1;min-width:180px}
.badge{display:inline-block;background:#16302e;color:var(--accent);border:1px solid var(--accent);
  border-radius:20px;padding:2px 12px;font-size:.75em;margin-left:6px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.on{background:var(--accent)}.off{background:#667}
.row{display:flex;align-items:center;margin:7px 0;font-size:.9em}
pre{background:var(--bg);padding:10px;border-radius:6px;font-size:.72em;max-height:180px;overflow:auto;color:#9ab;white-space:pre-wrap}
.flash{padding:11px;border-radius:6px;margin-bottom:14px;font-size:.9em;max-width:920px;margin-left:auto;margin-right:auto}
.fok{background:#10302b;color:var(--accent);border:1px solid var(--accent)}
.ferr{background:#301414;color:var(--danger);border:1px solid var(--danger)}
#spin{display:none;color:#9ab;margin-top:8px}
hr{border:none;border-top:1px solid var(--border);margin:16px 0}
</style></head><body>
<div class=topbar><a href=/logout class=logout>Déconnexion</a></div>
<div class=logo-top><img src=/assets/logo-minitel-gpt.png alt="MINITEL GPT"></div>
<main>
{% if flash %}<div class="flash {{'fok' if flash_ok else 'ferr'}}">{{flash}}</div>{% endif %}

<!-- PERSONNALITE -->
<div class=card>
  <h2>Personnalité du terminal</h2>
  <div class=toolbar>
    <select id=presetSel onchange=loadPreset()>
      {% for k,p in presets.items() %}
      <option value="{{k}}" {{'selected' if k==active_key}}>{{p.label}}{{' (active)' if k==active_key else ''}}</option>
      {% endfor %}
    </select>
    <button class="btn btn-s" type=button onclick=newPreset() style=margin:0>+ Nouveau</button>
  </div>
  <p class=sub id=activeInfo></p>
  <form method=POST action=/save-prompt id=editForm>
    <input type=hidden name=preset_key id=fkey>
    <label>Nom affiché</label>
    <input type=text name=label id=flabel>
    <label>Titre d'accueil (max 40)</label>
    <input type=text name=title_msg id=ftitle maxlength=40>
    <label>Phrase d'invite (max 40)</label>
    <input type=text name=question_msg id=fquestion maxlength=40>
    <label>Message d'attente (max 40)</label>
    <input type=text name=loading_msg id=floading maxlength=40>
    <label>Prompt système (consignes de l'IA)</label>
    <textarea name=system_prompt id=fsystem rows=12></textarea>
    <hr>
    <button class="btn btn-p">💾 Enregistrer</button>
    <button class="btn btn-s" formaction=/apply-preset>✓ Activer sur le Minitel</button>
    <button class="btn btn-d" formaction=/delete-preset onclick="return confirm('Supprimer ce preset ?')">Supprimer</button>
  </form>
</div>

<!-- GENERATION IA -->
<div class=card>
  <h2>✨ Générer un prompt par IA</h2>
  <p class=sub>Décrivez le projet ou le personnage : l'IA rédige les consignes. Elles rempliront le champ « Prompt système » ci-dessus, éditable avant d'enregistrer.</p>
  <textarea id=desc rows=4 placeholder="Ex: Un assistant qui ne parle que de cuisine italienne, ton chaleureux, refuse tout autre sujet..."></textarea>
  <button class="btn btn-p" type=button onclick=genPrompt()>Générer les consignes</button>
  <div id=spin>⏳ Génération en cours...</div>
</div>

<div class=grid>
  <!-- PARAMETRES -->
  <div class=card>
    <h2>Paramètres</h2>
    <form method=POST action=/save-key>
      <label>Clé API Anthropic</label>
      <p class=sub>Actuelle : {{key_masked}}</p>
      <input type=password name=anthropic_key placeholder="sk-ant-...">
      <button class="btn btn-p">Enregistrer la clé</button>
    </form>
    <hr>
    <form method=POST action=/save-mail>
      <label>Email de notification</label>
      <p class=sub>Reçoit l'IP du Minitel au démarrage et après config WiFi.</p>
      <input type=text name=mail_to value="{{mail_to}}" placeholder="vous@exemple.com">
      <button class="btn btn-p">Enregistrer l'email</button>
    </form>
  </div>

  <!-- SERVICES -->
  <div class=card>
    <h2>État des services</h2>
    {% for s,st in services.items() %}
    <div class=row><span class="dot {{'on' if st=='active' else 'off'}}"></span>{{s}}
      <span style="margin-left:auto;color:{{'#4ecdc4' if st=='active' else '#667'}}">{{st}}</span></div>
    {% endfor %}
    <form method=POST action=/restart style=margin-top:10px>
      <button class="btn btn-s">↺ Redémarrer le terminal</button></form>
    <p class=sub>oneshot (wifi/boot) « inactive » = normal après exécution.</p>
  </div>
</div>

<!-- LOGS -->
<div class=card>
  <h2>Logs terminal <a href=/ class=sub style=margin-left:8px>rafraîchir</a></h2>
  <pre>{{log_chatgpt}}</pre>
</div>
</main>

<script>
const PRESETS = {{presets_json|safe}};
const ACTIVE = {{active_key|tojson}};
function loadPreset(){
  const k = document.getElementById('presetSel').value;
  const p = PRESETS[k]; if(!p) return;
  document.getElementById('fkey').value = k;
  document.getElementById('flabel').value = p.label||'';
  document.getElementById('ftitle').value = p.title_msg||'';
  document.getElementById('fquestion').value = p.question_msg||'';
  document.getElementById('floading').value = p.loading_msg||'';
  document.getElementById('fsystem').value = p.system||'';
  document.getElementById('activeInfo').textContent =
    (k===ACTIVE) ? '● Ce preset est actuellement actif sur le Minitel.'
                 : 'Preset inactif. Cliquez « Activer » pour l\\'utiliser sur le Minitel.';
}
function newPreset(){
  const label = prompt("Nom du nouveau preset :");
  if(!label) return;
  const f = document.createElement('form'); f.method='POST'; f.action='/new-preset';
  const a=document.createElement('input'); a.name='label'; a.value=label; f.appendChild(a);
  const b=document.createElement('input'); b.name='key'; b.value=label.toLowerCase().replace(/[^a-z0-9]+/g,'_'); f.appendChild(b);
  document.body.appendChild(f); f.submit();
}
async function genPrompt(){
  const d=document.getElementById('desc').value.trim();
  if(!d){alert('Décrivez le projet d abord');return;}
  document.getElementById('spin').style.display='block';
  try{
    const r=await fetch('/generate-prompt',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({description:d})});
    const j=await r.json();
    if(j.ok){document.getElementById('fsystem').value=j.prompt;document.getElementById('fsystem').scrollIntoView({behavior:'smooth'});}
    else alert('Erreur: '+j.error);
  }catch(e){alert('Erreur: '+e);}
  document.getElementById('spin').style.display='none';
}
loadPreset();
</script>
</body></html>"""

# ── Routes ───────────────────────────────────────────────────────────────
@app.route("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(ASSETS_DIR, filename)

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "Mot de passe incorrect"
    return render_template_string(LOGIN_HTML, error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@require_login
def index():
    data = load_prompts()
    presets = normalized_presets(data)
    key = anthropic_key()
    masked = (key[:10] + "..." + key[-4:]) if len(key) > 14 else ("(définie)" if key else "(absente)")
    services = {"minitel-chatgpt": svc_status("minitel-chatgpt"),
                "wifi-manager": svc_status("wifi-manager"),
                "boot-notify": svc_status("boot-notify"),
                "admin-ui": svc_status("admin-ui")}
    flash = session.pop("flash", None); flash_ok = session.pop("flash_ok", False)
    return render_template_string(
        ADMIN_HTML, presets=presets, presets_json=json.dumps(presets),
        active_key=data["active"], services=services,
        log_chatgpt=log_tail("chatgpt"), key_masked=masked,
        mail_to=read_env().get("MAIL_TO", ""), flash=flash, flash_ok=flash_ok)

@app.route("/save-prompt", methods=["POST"])
@require_login
def save_prompt():
    data = load_prompts()
    k = request.form.get("preset_key", "").strip() or data["active"]
    p = data["presets"].setdefault(k, {})
    p["label"] = request.form.get("label", k).strip() or k
    p["title_msg"] = request.form.get("title_msg", DEFAULTS["title_msg"])[:40]
    p["question_msg"] = request.form.get("question_msg", DEFAULTS["question_msg"])[:40]
    p["loading_msg"] = request.form.get("loading_msg", DEFAULTS["loading_msg"])[:40]
    sp = request.form.get("system_prompt", "").strip()
    if sp:
        p["system"] = sp
    save_prompts(data)
    if k == data["active"]:
        restart_terminal()
    session["flash"] = f"Preset '{p['label']}' enregistré."
    session["flash_ok"] = True
    return redirect(url_for("index"))

@app.route("/apply-preset", methods=["POST"])
@require_login
def apply_preset():
    data = load_prompts()
    k = request.form.get("preset_key", "")
    # Sauvegarder aussi les modifications en cours avant d'activer
    if k in data["presets"]:
        p = data["presets"][k]
        if request.form.get("label"):
            p["label"] = request.form.get("label").strip()
            p["title_msg"] = request.form.get("title_msg", DEFAULTS["title_msg"])[:40]
            p["question_msg"] = request.form.get("question_msg", DEFAULTS["question_msg"])[:40]
            p["loading_msg"] = request.form.get("loading_msg", DEFAULTS["loading_msg"])[:40]
            if request.form.get("system_prompt", "").strip():
                p["system"] = request.form.get("system_prompt").strip()
        data["active"] = k
        save_prompts(data)
        restart_terminal()
        session["flash"] = f"Preset '{p.get('label', k)}' activé sur le Minitel."
        session["flash_ok"] = True
    return redirect(url_for("index"))

@app.route("/new-preset", methods=["POST"])
@require_login
def new_preset():
    data = load_prompts()
    key = re.sub(r"[^a-z0-9_]", "_", request.form.get("key", "").strip().lower())
    label = request.form.get("label", "").strip()
    if not key:
        session["flash"] = "Identifiant invalide."; session["flash_ok"] = False
    elif key in data["presets"]:
        session["flash"] = "Cet identifiant existe déjà."; session["flash_ok"] = False
    else:
        data["presets"][key] = {"label": label or key,
                                "system": "Tu es un assistant. Reponds en ASCII sans accents, concis.",
                                **DEFAULTS}
        save_prompts(data)
        session["flash"] = f"Preset '{label}' créé. Éditez-le puis Activez-le."
        session["flash_ok"] = True
    return redirect(url_for("index"))

@app.route("/delete-preset", methods=["POST"])
@require_login
def delete_preset():
    data = load_prompts()
    k = request.form.get("preset_key", "")
    if len(data["presets"]) <= 1:
        session["flash"] = "Impossible de supprimer le dernier preset."; session["flash_ok"] = False
    elif k in data["presets"]:
        del data["presets"][k]
        if data["active"] == k:
            data["active"] = next(iter(data["presets"]))
            save_prompts(data)
            restart_terminal()
        else:
            save_prompts(data)
        session["flash"] = "Preset supprimé."; session["flash_ok"] = True
    return redirect(url_for("index"))

@app.route("/generate-prompt", methods=["POST"])
@require_login
def gen_prompt_route():
    desc = (request.json or {}).get("description", "").strip()
    if not desc:
        return jsonify(ok=False, error="Description vide")
    try:
        return jsonify(ok=True, prompt=generate_prompt(desc))
    except Exception as e:
        return jsonify(ok=False, error=str(e))

@app.route("/save-key", methods=["POST"])
@require_login
def save_key():
    key = request.form.get("anthropic_key", "").strip()
    if key:
        write_env_key("ANTHROPIC_KEY", key)
        restart_terminal()
        session["flash"] = "Clé Anthropic enregistrée et terminal redémarré."
        session["flash_ok"] = True
    else:
        session["flash"] = "Clé vide."; session["flash_ok"] = False
    return redirect(url_for("index"))

@app.route("/save-mail", methods=["POST"])
@require_login
def save_mail():
    mail = request.form.get("mail_to", "").strip()
    if mail and "@" in mail:
        write_env_key("MAIL_TO", mail)
        restart_terminal()
        session["flash"] = f"Email de notification : {mail}"
        session["flash_ok"] = True
    else:
        session["flash"] = "Adresse email invalide."; session["flash_ok"] = False
    return redirect(url_for("index"))

@app.route("/restart", methods=["POST"])
@require_login
def restart():
    ok = restart_terminal()
    session["flash"] = "Terminal redémarré." if ok else "Échec (sudo requis)."
    session["flash_ok"] = ok
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
