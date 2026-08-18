#!/usr/bin/env python3
"""Printable PaperTrace canvases: 4 tags clustered tightly around a postcard-
sized painting area, framed to show where the reference image lands (stretched
to fill, corner to corner). No cutting or sticking required."""
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

MARKER_MM = 20                      # small: tags are close to the frame, not the page edge
QUIET = MARKER_MM * 0.24
STICKER = MARKER_MM + 2 * QUIET
TAG_GAP = 5 * mm                    # frame-to-tag gap - tight cluster stays in one camera shot

FRAME_W, FRAME_H = 101.6 * mm, 152.4 * mm   # 4x6in postcard, portrait


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


def draw_frame(c, x, y, w, h):
    """A clean double-rule border showing where the traced image goes."""
    c.saveState()
    c.setStrokeColorRGB(0.1, 0.1, 0.1)
    c.setLineWidth(1.1)
    c.rect(x, y, w, h, stroke=1, fill=0)
    inset = 2.2 * mm
    c.setLineWidth(0.5)
    c.setStrokeColorRGB(0.5, 0.5, 0.5)
    c.rect(x + inset, y + inset, w - 2 * inset, h - 2 * inset, stroke=1, fill=0)
    c.restoreState()


def build(path, pagesize, label):
    w, h = pagesize
    marker = MARKER_MM * mm
    quiet = QUIET * mm
    sticker = STICKER * mm

    fx = (w - FRAME_W) / 2
    fy = (h - FRAME_H) / 2 + 4 * mm   # nudge up a hair to leave room for the footer caption

    c = canvas.Canvas(path, pagesize=pagesize)
    c.setTitle("PaperTrace AR canvas")

    draw_frame(c, fx, fy, FRAME_W, FRAME_H)

    corners = [
        (fx - TAG_GAP - sticker, fy + FRAME_H + TAG_GAP, CODES[0]),              # top-left
        (fx + FRAME_W + TAG_GAP, fy + FRAME_H + TAG_GAP, CODES[1]),              # top-right
        (fx - TAG_GAP - sticker, fy - TAG_GAP - sticker, CODES[2]),              # bottom-left
        (fx + FRAME_W + TAG_GAP, fy - TAG_GAP - sticker, CODES[3]),              # bottom-right
    ]
    for x, y, code in corners:
        draw_marker(c, code, x + quiet, y + quiet, marker)

    c.setFont("Helvetica", 7)
    c.setFillColorRGB(0.6, 0.6, 0.6)
    c.drawCentredString(w / 2, 10 * mm,
                         f"PaperTrace AR canvas · {label} · tags 0-3 · your image stretches to fill the frame")
    c.save()
    print("wrote", path, f"({label})")


build(os.path.join(BUILD, "papertrace-canvas-A4.pdf"), A4, "A4")
build(os.path.join(BUILD, "papertrace-canvas-Letter.pdf"), letter, "Letter")
