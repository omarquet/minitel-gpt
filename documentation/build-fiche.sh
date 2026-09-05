#!/bin/sh
# Genere la fiche d'atelier imprimable a partir de sa source Markdown.
#
#   documentation/fiche-atelier.md  ->  pandoc  ->  HTML  ->  Chrome  ->  PDF
#
# La source a maintenir est le .md ; le PDF est un produit, regenere a chaque
# modification. Pas de LaTeX dans la chaine : Chrome imprime le HTML, ce qui
# donne les memes tableaux et les memes schemas ASCII qu'a l'ecran.
#
# Aucune police a telecharger : la fiche s'appuie sur les polices du systeme,
# la generation marche donc hors ligne.
#
# Prerequis : pandoc (brew install pandoc) et Google Chrome.
set -e
cd "$(dirname "$0")"

SRC=fiche-atelier.md
PDF=fiche-atelier.pdf
HTML=$(mktemp -t fiche-atelier).html
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[ -x "$CHROME" ] || CHROME="/Applications/Chromium.app/Contents/MacOS/Chromium"
[ -x "$CHROME" ] || { echo "Chrome introuvable : adapter la variable CHROME."; exit 1; }

# --embed-resources : la CSS et les polices entrent dans le HTML, Chrome n'a
# donc rien a aller chercher a cote du fichier temporaire.
pandoc "$SRC" \
  --standalone --embed-resources \
  --from markdown --to html5 \
  --css fiche-atelier.css \
  -o "$HTML"

"$CHROME" --headless --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="$PWD/$PDF" "file://$HTML" 2>/dev/null

rm -f "$HTML"
echo "OK : $PWD/$PDF"
