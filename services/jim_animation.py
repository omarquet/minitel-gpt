#!/usr/bin/env python3
"""
Animation d'accueil style Minitel — portrait de Jim.
Effet "élection Mitterrand 1981" : révélation ligne par ligne à 1200 baud.
À 1200 baud / 7E1, chaque caractère prend ~8.3ms → 40 colonnes = ~333ms/ligne
→ 20 lignes = ~7 secondes de révélation progressive, sans délai artificiel.
"""
import time
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from jim_ascii import JIM_ASCII, JIM_LABEL
from minitel_serial import MinitelSerial

COLS = 40


def center(text: str, width: int = COLS) -> str:
    """Centre un texte sur width colonnes (troncature si trop long)."""
    text = text[:width]
    pad = (width - len(text)) // 2
    return " " * pad + text


def play_mitterrand_intro(m: MinitelSerial):
    """
    Affiche le portrait de Jim en animation progressive façon Mitterrand 81.
    Le Minitel reçoit les caractères à 1200 baud : l'effet est naturel.
    """
    m.clear_screen()
    m.cursor_home()

    # ── Ligne de titre animée (avant le portrait) ──────────────────────────
    title = center("10 MAI 1981  -  SPECIAL SOIREE")
    for char in title:
        m.send_bytes(char.encode("ascii", errors="replace"))
        # Pas de sleep : la baud rate crée l'effet
    m.newline()

    sep = "=" * COLS
    m.send_text(sep + "\r\n")

    # ── Portrait ligne par ligne ────────────────────────────────────────────
    # À 1200 baud le buffer série envoie ~120 chars/sec.
    # Chaque ligne de 40 chars s'affiche en ~330ms → effet de balayage naturel.
    for i, line in enumerate(JIM_ASCII):
        m.send_text(line + "\r\n")
        # Petite pause dramatique tous les 5 lignes (comme un souffle coupé)
        if i in (4, 9, 14):
            time.sleep(0.15)

    # ── Message sous le portrait ────────────────────────────────────────────
    m.send_text(sep + "\r\n")
    time.sleep(0.3)

    # Message centré, lettre par lettre façon télescripteur
    label_centered = center(JIM_LABEL)
    for char in label_centered:
        m.send_bytes(char.encode("ascii", errors="replace"))
        time.sleep(0.06)  # Effet machine à écrire sur le message clé
    m.newline()

    time.sleep(0.3)

    # Sous-titre
    sub = center("CANAL+ DES ANNEES 80 - BONNE ANNEE !")
    m.send_text(sub + "\r\n")
    time.sleep(0.5)

    m.send_text(sep + "\r\n")
    time.sleep(0.2)

    # Invite à démarrer
    invite = center("Appuyez sur ENTREE pour dialoguer")
    m.send_text(invite + "\r\n")


def play_interlude(m: MinitelSerial):
    """
    Animation courte entre deux questions — affiche le portrait en 2 secondes
    puis revient à l'invite. Appelé à chaque nouvelle conversation.
    """
    m.clear_screen()
    m.cursor_home()

    sep = "-" * COLS
    m.send_text(sep + "\r\n")
    for line in JIM_ASCII:
        m.send_text(line + "\r\n")
    m.send_text(sep + "\r\n")
    label = center(JIM_LABEL)
    m.send_text(label + "\r\n")
    m.send_text(sep + "\r\n")
    time.sleep(0.5)


if __name__ == "__main__":
    # Test standalone
    m = MinitelSerial()
    m.open()
    play_mitterrand_intro(m)
    m.close()
