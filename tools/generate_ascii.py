#!/usr/bin/env python3
"""
Convertit jim.jpg en ASCII art 40 colonnes pour Minitel.
v2 — zoom sur le visage + contraste amélioré
"""
import sys, os
try:
    from PIL import Image, ImageEnhance, ImageFilter
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow",
                           "--break-system-packages", "-q"])
    from PIL import Image, ImageEnhance, ImageFilter

IMG_PATH = os.path.join(os.path.dirname(__file__), "..", "jim2.jpg")
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "services", "jim_ascii.py")

COLS = 40
ROWS = 22

# Photo jim2 : déjà N&B, fond blanc, fort contraste.
# Palette : sombre → dense, clair (fond blanc) → espace.
CHARS = "@#MW8Bboar*+=:- ."

def brightness_to_char(b: int) -> str:
    idx = int(b / 255 * (len(CHARS) - 1))
    return CHARS[idx]

def image_to_ascii(path: str, cols: int, rows: int) -> list[str]:
    from PIL import ImageOps
    img = Image.open(path).convert("L")  # déjà N&B
    w, h = img.size

    # Photo circulaire centrée sur fond blanc — recadrer légèrement
    crop = img.crop((
        int(w * 0.04),
        int(h * 0.02),
        int(w * 0.96),
        int(h * 0.98),
    ))

    # Pas besoin d'augmenter le contraste — la photo est déjà très contrastée.
    # Légère accentuation de la netteté seulement.
    crop = ImageEnhance.Sharpness(crop).enhance(1.5)
    crop = ImageOps.autocontrast(crop, cutoff=1)

    # Redimensionner (compensation aspect ratio char ASCII ~0.5)
    crop = crop.resize((cols, rows * 2), Image.LANCZOS)

    lines = []
    for row in range(rows):
        line = ""
        for col in range(cols):
            b = crop.getpixel((col, row * 2))
            line += brightness_to_char(b)
        lines.append(line)
    return lines

def main():
    print(f"Conversion de {IMG_PATH} (v2 — zoom visage)...")
    lines = image_to_ascii(IMG_PATH, COLS, ROWS)

    print("\n" + "=" * COLS)
    for line in lines:
        print(line)
    print("=" * COLS + "\n")

    escaped = [repr(line) for line in lines]
    content = f'''# Auto-généré par generate_ascii.py v2 — portrait de Jim
JIM_ASCII = [
{chr(10).join("    " + e + "," for e in escaped)}
]

JIM_LABEL = "*** BON ANNIVERSAIRE JIM ! ***"
'''
    with open(OUT_PATH, "w") as f:
        f.write(content)
    print(f"Généré : {OUT_PATH} ({COLS}x{len(lines)})")

if __name__ == "__main__":
    main()
