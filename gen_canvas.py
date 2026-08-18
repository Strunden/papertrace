#!/usr/bin/env python3
"""Printable PaperTrace canvases: tags pre-placed at the four page corners,
blank paper in between to draw on. No cutting or sticking required."""
import json, os
import numpy as np
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.units import mm

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
DICT = json.load(open(os.path.join(BUILD, "dictionary.json")))
N = DICT["gridSize"]
MODULES = DICT["modules"]
CODES = DICT["codes"]

MARKER_MM = 25
QUIET = MARKER_MM * 0.24
STICKER = MARKER_MM + 2 * QUIET
MARGIN = 10 * mm


def grid(code):
    g = np.zeros((MODULES, MODULES), dtype=np.uint8)   # 0 = black
    for r in range(N):
        for c in range(N):
            g[r + 1, c + 1] = (code >> (N * N - 1 - (r * N + c))) & 1
    return g


def draw_marker(c, code, x, y, size):
    """Draw marker with lower-left corner at (x,y), edge length `size`."""
    g = grid(code)
    m = size / MODULES
    c.setFillColorRGB(0, 0, 0)
    for r in range(MODULES):
        for col in range(MODULES):
            if g[r, col] == 0:
                c.rect(x + col * m, y + (MODULES - 1 - r) * m, m * 1.002, m * 1.002,
                       stroke=0, fill=1)


def build(path, pagesize, label):
    w, h = pagesize
    marker = MARKER_MM * mm
    quiet = QUIET * mm
    sticker = STICKER * mm
    c = canvas.Canvas(path, pagesize=pagesize)
    c.setTitle("PaperTrace AR canvas")

    corners = [
        (MARGIN, h - MARGIN - sticker, CODES[0]),              # top-left
        (w - MARGIN - sticker, h - MARGIN - sticker, CODES[1]),  # top-right
        (MARGIN, MARGIN, CODES[2]),                             # bottom-left
        (w - MARGIN - sticker, MARGIN, CODES[3]),               # bottom-right
    ]
    for x, y, code in corners:
        draw_marker(c, code, x + quiet, y + quiet, marker)

    c.setFont("Helvetica", 7)
    c.setFillColorRGB(0.6, 0.6, 0.6)
    c.drawCentredString(w / 2, MARGIN / 2,
                         f"PaperTrace AR canvas · {label} · tags 0-3 · draw inside the tags")
    c.save()
    print("wrote", path, f"({label})")


build(os.path.join(BUILD, "papertrace-canvas-A4.pdf"), A4, "A4")
build(os.path.join(BUILD, "papertrace-canvas-Letter.pdf"), letter, "Letter")
