#!/usr/bin/env python3
"""
Convertit jim.jpg en ASCII art 40 colonnes pour Minitel.
Usage : python3 generate_ascii.py
Sortie : affiche l'ASCII art + génère jim_ascii.py
"""
import sys
try:
    from PIL import Image
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow",
                           "--break-system-packages", "-q"])
    from PIL import Image

import os

# Chemin de l'image
IMG_PATH = os.path.join(os.path.dirname(__file__), "..", "jim.jpg")
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "services", "jim_ascii.py")

# Dimensions Minitel : 40 colonnes, on laisse 4 lignes pour header/footer
COLS = 40
ROWS = 20

# Caractères du plus foncé au plus clair (adapté Minitel 7-bit ASCII)
# Densité visuelle décroissante
CHARS = "@%#*+=-:. "

def brightness_to_char(b: int) -> str:
    """Convertit une luminosité 0-255 en caractère ASCII."""
    idx = int(b / 255 * (len(CHARS) - 1))
    return CHARS[idx]

def image_to_ascii(path: str, cols: int, rows: int) -> list[str]:
    img = Image.open(path).convert("L")  # Niveaux de gris

    # Recadrer sur la partie haute (visage) — 80% de l'image
    w, h = img.size
    crop_h = int(h * 0.88)
    img = img.crop((0, 0, w, crop_h))

    # Les caractères ASCII sont environ 2× plus hauts que larges
    # → doubler le nombre de lignes pour compenser l'aspect ratio
    img = img.resize((cols, rows * 2), Image.LANCZOS)

    # Sous-échantillonner verticalement (garder 1 ligne sur 2)
    lines = []
    for row in range(rows):
        line = ""
        for col in range(cols):
            b = img.getpixel((col, row * 2))
            line += brightness_to_char(b)
        lines.append(line)
    return lines

def main():
    print(f"Conversion de {IMG_PATH}...")
    lines = image_to_ascii(IMG_PATH, COLS, ROWS)

    # Affichage preview
    print("\n" + "=" * COLS)
    for line in lines:
        print(line)
    print("=" * COLS + "\n")

    # Génération du fichier Python
    escaped = [repr(line) for line in lines]
    content = f'''# Auto-généré par generate_ascii.py — portrait de Jim pour le Minitel
JIM_ASCII = [
{chr(10).join("    " + e + "," for e in escaped)}
]

# Label affiché sous le portrait
JIM_LABEL = "*** BON ANNIVERSAIRE JIM ! ***"
'''
    with open(OUT_PATH, "w") as f:
        f.write(content)

    print(f"Fichier généré : {OUT_PATH}")
    print(f"Dimensions : {COLS}×{len(lines)} caractères")

if __name__ == "__main__":
    main()
