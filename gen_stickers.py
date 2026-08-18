#!/usr/bin/env python3
"""Printable ArUco-style sticker sheets for PaperTrace AR."""
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
                # PDF y axis points up; grid row 0 is the top row
                c.rect(x + col * m, y + (MODULES - 1 - r) * m, m * 1.002, m * 1.002,
                       stroke=0, fill=1)


def cut_square(c, x, y, s):
    c.saveState()
    c.setStrokeColorRGB(0.72, 0.72, 0.72)
    c.setLineWidth(0.4)
    c.setDash(2, 2)
    c.rect(x, y, s, s, stroke=1, fill=0)
    c.restoreState()


def sheet_page(c, page_w, page_h, marker_mm, title):
    marker = marker_mm * mm
    quiet = marker * 0.24                 # white quiet zone (needed for detection)
    sticker = marker + 2 * quiet
    gutter = 7 * mm
    pitch = sticker + gutter

    top_margin = 22 * mm
    side_margin = 10 * mm
    usable_w = page_w - 2 * side_margin
    usable_h = page_h - top_margin - 12 * mm
    cols = max(1, int((usable_w + gutter) // pitch))
    rows = max(1, int((usable_h + gutter) // pitch))

    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(side_margin, page_h - 14 * mm, title)
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.35, 0.35, 0.35)
    c.drawString(side_margin, page_h - 19 * mm,
                 "Print at 100% scale (no 'fit to page'). Cut on the dashed lines - "
                 "the white border is required for tracking.")

    grid_w = cols * pitch - gutter
    x0 = (page_w - grid_w) / 2
    y_top = page_h - top_margin

    n = 0
    for r in range(rows):
        for col in range(cols):
            if n >= len(CODES):
                break
            x = x0 + col * pitch
            y = y_top - (r + 1) * sticker - r * gutter
            if y < 12 * mm:
                break
            cut_square(c, x, y, sticker)
            draw_marker(c, CODES[n], x + quiet, y + quiet, marker)
            c.setFillColorRGB(0.55, 0.55, 0.55)
            c.setFont("Helvetica", 6)
            c.drawCentredString(x + sticker / 2, y - 4.6 * mm, f"tag {n}")
            n += 1
    return n


def instructions(c, page_w, page_h):
    L = 18 * mm
    y = page_h - 24 * mm
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(L, y, "PaperTrace AR - tracking stickers")
    y -= 11 * mm
    c.setFont("Helvetica", 10.5)
    lines = [
        "These tags anchor your reference image to the paper. Whenever the camera can see at least one",
        "tag, the overlay stays locked to the page - correct position, scale and perspective - even as you",
        "move the phone around with your hand.",
        "",
        "HOW TO USE",
        "1.  Print this file at 100% scale. Plain paper works; sticker/label paper is nicer.",
        "2.  Cut along the dashed lines. Keep the white border - the tracker needs it.",
        "3.  Stick 3-6 tags around (or just outside) your drawing area. Any arrangement is fine; they do",
        "     not need to be square, evenly spaced, or all the same size.",
        "4.  Open the app, allow camera access, and point the phone at the paper.",
        "5.  Sweep the phone slowly across all your tags once. The app learns where they sit relative to",
        "     each other, so afterwards a single visible tag is enough to hold the overlay in place.",
        "6.  Pick an image, choose a trace style, drag/pinch it into position, then hit Lock and draw.",
        "",
        "TIPS FOR STABLE TRACKING",
        "-  KEEP TWO TAGS IN VIEW whenever you can. This matters more than anything else here.",
        "     Two tags pin the perspective down properly. With only one, the tilt of the page has to",
        "     be inferred from four corners, and a fraction of a pixel of measurement error gets",
        "     amplified the further you look from that tag - so the overlay wanders by a millimetre",
        "     or two. It still works, it is just visibly less steady.",
        "-  Spread tags out around the drawing so your hand never covers all of them at once.",
        "-  Even, diffuse light. Avoid glare and hard shadows falling across a tag.",
        "-  Matte paper beats glossy. Glossy label stock reflects and blinds the tracker.",
        "-  Bigger tags track from further away. The 45 mm page is good for easels; 20 mm for A5 work.",
        "-  Keep tags flat. A curled tag bends the perspective estimate.",
        "-  If the overlay ever drifts, tap Reset anchors and sweep across the tags again.",
        "",
        "WHICH PAGE TO PRINT",
        "-  Page 2 - 30 mm tags: the all-round default for A4 / Letter drawings on a desk.",
        "-  Page 3 - 20 mm tags: small sketchbooks, close-up work.",
        "-  Page 4 - 45 mm tags: easels, large paper, or working from further back.",
        "",
        "Each tag carries a different ID, so mixing sizes and pages on one setup is fine - just never",
        "put two copies of the same tag number in view at the same time.",
    ]
    for t in lines:
        if t in ("HOW TO USE", "TIPS FOR STABLE TRACKING", "WHICH PAGE TO PRINT"):
            c.setFont("Helvetica-Bold", 10.5)
            y -= 2 * mm
        else:
            c.setFont("Helvetica", 10.5)
        c.drawString(L, y, t)
        y -= 5.4 * mm


def build(path, pagesize, label):
    w, h = pagesize
    c = canvas.Canvas(path, pagesize=pagesize)
    c.setTitle("PaperTrace AR tracking stickers")
    instructions(c, w, h)
    c.showPage()
    for size, name in ((30, "30 mm tags - default"), (20, "20 mm tags - small paper"),
                       (45, "45 mm tags - easel / long range")):
        sheet_page(c, w, h, size, f"PaperTrace AR  -  {name}")
        c.showPage()
    c.save()
    print("wrote", path, f"({label})")


build(os.path.join(BUILD, "papertrace-markers-A4.pdf"), A4, "A4")
build(os.path.join(BUILD, "papertrace-markers-Letter.pdf"), letter, "Letter")
