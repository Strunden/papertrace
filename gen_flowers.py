#!/usr/bin/env python3
"""Procedurally author clean flower line-art as SVG, for PaperTrace AR's seed library."""
import math, os, json

W = 1000
HALF = W / 2
BUILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build")
os.makedirs(BUILD, exist_ok=True)


# ------------------------------------------------------------------ geometry
def rot(v, a):
    c, s = math.cos(a), math.sin(a)
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)


def add(*pts):
    return (sum(p[0] for p in pts), sum(p[1] for p in pts))


def mul(v, k):
    return (v[0] * k, v[1] * k)


def f(p):
    return f"{p[0]:.1f},{p[1]:.1f}"


def petal(base, angle, length, halfw, waist=0.15, tip=0.62, curl=0.0):
    """Closed teardrop petal from `base` pointing at `angle` (radians, 0 = right)."""
    d = (math.cos(angle), math.sin(angle))
    n = (-d[1], d[0])
    t = add(base, mul(d, length), mul(n, curl * length))
    c1 = add(base, mul(d, waist * length), mul(n, halfw * 0.95))
    c2 = add(base, mul(d, tip * length), mul(n, halfw + curl * length * 0.5))
    c3 = add(base, mul(d, tip * length), mul(n, -halfw + curl * length * 0.5))
    c4 = add(base, mul(d, waist * length), mul(n, -halfw * 0.95))
    return (f"M{f(base)} C{f(c1)} {f(c2)} {f(t)} "
            f"C{f(c3)} {f(c4)} {f(base)} Z")


def pointed_petal(base, angle, length, halfw, bulge=0.55):
    """Petal with a sharp tip (lotus / lily style)."""
    d = (math.cos(angle), math.sin(angle))
    n = (-d[1], d[0])
    t = add(base, mul(d, length))
    c1 = add(base, mul(d, 0.12 * length), mul(n, halfw))
    c2 = add(base, mul(d, bulge * length), mul(n, halfw * 0.92))
    c3 = add(base, mul(d, bulge * length), mul(n, -halfw * 0.92))
    c4 = add(base, mul(d, 0.12 * length), mul(n, -halfw))
    return (f"M{f(base)} C{f(c1)} {f(c2)} {f(t)} C{f(c3)} {f(c4)} {f(base)} Z")


def vein(base, angle, length, curl=0.0):
    d = (math.cos(angle), math.sin(angle))
    n = (-d[1], d[0])
    t = add(base, mul(d, length), mul(n, curl * length))
    c1 = add(base, mul(d, length * 0.4), mul(n, curl * length * 0.2))
    c2 = add(base, mul(d, length * 0.75), mul(n, curl * length * 0.7))
    return f"M{f(base)} C{f(c1)} {f(c2)} {f(t)}"


def circle(cx, cy, r):
    return (f"M{cx - r:.1f},{cy:.1f} a{r:.1f},{r:.1f} 0 1,0 {2 * r:.1f},0 "
            f"a{r:.1f},{r:.1f} 0 1,0 {-2 * r:.1f},0 Z")


def leaf(base, angle, length, halfw, curl=0.25):
    d = (math.cos(angle), math.sin(angle))
    n = (-d[1], d[0])
    t = add(base, mul(d, length), mul(n, curl * length))
    c1 = add(base, mul(d, 0.25 * length), mul(n, halfw))
    c2 = add(base, mul(d, 0.72 * length), mul(n, halfw * 0.8 + curl * length * 0.4))
    c3 = add(base, mul(d, 0.72 * length), mul(n, -halfw * 0.8 + curl * length * 0.6))
    c4 = add(base, mul(d, 0.25 * length), mul(n, -halfw))
    return f"M{f(base)} C{f(c1)} {f(c2)} {f(t)} C{f(c3)} {f(c4)} {f(base)} Z"


def stem(x0, y0, x1, y1, bend=40):
    mx, my = (x0 + x1) / 2 + bend, (y0 + y1) / 2
    return f"M{x0:.1f},{y0:.1f} Q{mx:.1f},{my:.1f} {x1:.1f},{y1:.1f}"


def spiral(cx, cy, r0, r1, turns, steps=140, phase=0.0):
    pts = []
    for i in range(steps + 1):
        u = i / steps
        a = phase + u * turns * 2 * math.pi
        r = r0 + (r1 - r0) * u
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return "M" + " L".join(f(p) for p in pts)


# ------------------------------------------------------------------- flowers
def cup_arc(cx, cy, R, a, span, bulge):
    """Open arc bulging outward - reads as one layered petal edge."""
    p0 = add((cx, cy), mul(rot((1, 0), a - span / 2), R))
    p1 = add((cx, cy), mul(rot((1, 0), a + span / 2), R))
    c = add((cx, cy), mul(rot((1, 0), a), R * bulge))
    return f"M{f(p0)} Q{f(c)} {f(p1)}"


def rose():
    """Layered open arcs around a log-spiral heart - avoids the petal spaghetti
    you get from stacking closed teardrops."""
    p = []
    cx, cy = HALF, 385
    pts = []
    for i in range(141):
        t = i / 140 * 3.2 * 2 * math.pi
        r = 10 * math.exp(0.110 * t)
        pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    p.append("M" + " L".join(f(q) for q in pts))
    for R, n, phase in ((112, 5, 0.0), (170, 6, 0.5), (230, 7, 0.25)):
        for i in range(n):
            a = -math.pi / 2 + (i + phase) * 2 * math.pi / n
            span = 2 * math.pi / n * 1.18
            p.append(cup_arc(cx, cy, R, a, span, 1.44))
            p.append(cup_arc(cx, cy, R * 0.90, a, span * 0.78, 1.34))
    for i in range(5):                                   # sepals
        a = math.pi / 2 + (i - 2) * math.radians(30)
        p.append(pointed_petal(add((cx, cy), mul(rot((1, 0), a), 240)), a, 118, 34, bulge=0.6))
    p.append(stem(cx - 4, cy + 300, cx + 26, 935, bend=-44))
    p.append(leaf((cx + 4, 740), math.radians(202), 190, 62, curl=0.30))
    p.append(leaf((cx + 16, 838), math.radians(-22), 172, 56, curl=-0.30))
    p.append(vein((cx + 4, 740), math.radians(202), 150, 0.30))
    p.append(vein((cx + 16, 838), math.radians(-22), 136, -0.30))
    return p


def sunflower():
    p = []
    cx, cy = HALF, 400
    for i in range(16):                                 # back row
        a = i * 2 * math.pi / 16 + math.pi / 16
        p.append(petal(add((cx, cy), mul((math.cos(a), math.sin(a)), 150)),
                       a, 235, 52, curl=0.05))
    for i in range(16):                                 # front row
        a = i * 2 * math.pi / 16
        p.append(petal(add((cx, cy), mul((math.cos(a), math.sin(a)), 142)),
                       a, 205, 60, curl=-0.05))
        p.append(vein(add((cx, cy), mul((math.cos(a), math.sin(a)), 155)), a, 165))
    p.append(circle(cx, cy, 152))
    p.append(circle(cx, cy, 132))
    golden = math.pi * (3 - math.sqrt(5))               # phyllotaxis seed head
    for i in range(1, 150):
        r = 126 * math.sqrt(i / 150)
        a = i * golden
        p.append(circle(cx + r * math.cos(a), cy + r * math.sin(a), 6.5))
    p.append(stem(cx, cy + 350, cx - 8, 960, bend=34))
    p.append(leaf((cx + 8, 680), math.radians(-15), 215, 88, curl=-0.24))
    p.append(leaf((cx - 2, 800), math.radians(196), 200, 82, curl=0.24))
    p.append(vein((cx + 8, 680), math.radians(-15), 190, -0.24))
    p.append(vein((cx - 2, 800), math.radians(196), 178, 0.24))
    return p


def tulip():
    """Explicit cup silhouette - three petals reading front-to-back."""
    p = []
    p.append("M500,585 C368,566 330,432 352,258")           # outer left wall
    p.append("M500,585 C632,566 670,432 648,258")           # outer right wall
    p.append("M352,258 C374,306 420,340 468,352")           # left petal inner edge
    p.append("M648,258 C626,306 580,340 532,352")           # right petal inner edge
    p.append("M468,352 C448,248 466,186 500,146 "
             "C534,186 552,248 532,352")                    # front petal
    p.append("M468,352 C488,362 512,362 532,352")           # where petals meet
    p.append("M416,540 C464,568 536,568 584,540")           # base of the cup
    p.append("M500,180 C494,250 494,300 500,344")           # single soft crease
    p.append(stem(500, 585, 512, 952, bend=24))
    p.append(leaf((504, 706), math.radians(212), 300, 62, curl=0.34))
    p.append(leaf((508, 786), math.radians(-32), 282, 56, curl=-0.34))
    p.append(vein((504, 706), math.radians(212), 250, 0.34))
    p.append(vein((508, 786), math.radians(-32), 236, -0.34))
    return p


def daisy():
    p = []
    cx, cy = HALF, 390
    for row, (n, ln, hw, off) in enumerate(((14, 250, 44, 0.0), (14, 215, 40, 0.5))):
        for i in range(n):
            a = (i + off) * 2 * math.pi / n
            b = add((cx, cy), mul((math.cos(a), math.sin(a)), 92))
            p.append(petal(b, a, ln, hw, curl=0.06 if row else -0.06))
            if row == 0:
                p.append(vein(add((cx, cy), mul((math.cos(a), math.sin(a)), 105)), a, 195))
    p.append(circle(cx, cy, 96))
    p.append(circle(cx, cy, 78))
    golden = math.pi * (3 - math.sqrt(5))
    for i in range(1, 60):
        r = 70 * math.sqrt(i / 60)
        a = i * golden
        p.append(circle(cx + r * math.cos(a), cy + r * math.sin(a), 6))
    p.append(stem(cx, cy + 300, cx - 4, 950, bend=-30))
    p.append(leaf((cx - 12, 700), math.radians(-22), 175, 52, curl=-0.3))
    p.append(leaf((cx - 6, 810), math.radians(204), 165, 48, curl=0.3))
    return p


def lotus():
    p = []
    cx, cy = HALF, 545
    for i in range(7):                                   # back row, widest fan
        a = -math.pi / 2 + (i - 3) * math.radians(26)
        p.append(pointed_petal((cx, cy), a, 310, 56, bulge=0.50))
    for i in range(5):                                   # middle row
        a = -math.pi / 2 + (i - 2) * math.radians(29)
        p.append(pointed_petal((cx, cy - 14), a, 232, 60, bulge=0.53))
    for i in range(3):                                   # inner cup
        a = -math.pi / 2 + (i - 1) * math.radians(31)
        p.append(pointed_petal((cx, cy - 26), a, 155, 54, bulge=0.56))
    for s in (-1, 1):                                    # two petals lying outward
        p.append(pointed_petal((cx + s * 26, cy + 4),
                               math.radians(0 if s > 0 else 180) - s * math.radians(12),
                               250, 58, bulge=0.50))
    p.append(f"M{cx - 96},{cy + 26} Q{cx},{cy + 74} {cx + 96},{cy + 26}")   # calyx
    for yy, x0, x1 in ((712, 150, 470), (712, 560, 890),
                       (782, 220, 780), (846, 300, 700)):                    # water
        p.append(f"M{x0},{yy} Q{(x0 + x1) / 2:.0f},{yy + 22} {x1},{yy}")
    return p


def cherry_blossom():
    p = []
    p.append(f"M60,880 C240,830 300,760 420,700 C560,630 700,600 940,560")   # branch
    p.append(f"M64,900 C244,850 306,780 426,720 C566,650 706,620 946,580")
    p.append(f"M330,742 C380,700 420,660 430,600")                            # twigs
    p.append(f"M620,632 C660,600 690,560 700,505")
    p.append(f"M770,600 C800,640 840,668 890,676")

    def blossom(cx, cy, r, phase=0.0):
        out = []
        for i in range(5):
            a = phase + i * 2 * math.pi / 5
            b = add((cx, cy), mul((math.cos(a), math.sin(a)), r * 0.20))
            out.append(petal(b, a, r, r * 0.52, curl=0.0, tip=0.70))
            tipp = add((cx, cy), mul((math.cos(a), math.sin(a)), r * 1.16))
            nn = (-math.sin(a), math.cos(a))
            out.append(f"M{f(add(tipp, mul(nn, r * 0.12)))} Q{f(add((cx, cy), mul((math.cos(a), math.sin(a)), r * 1.02)))} "
                       f"{f(add(tipp, mul(nn, -r * 0.12)))}")               # notched tip
        for i in range(8):                                                   # stamens
            a = phase + i * 2 * math.pi / 8 + 0.2
            e = add((cx, cy), mul((math.cos(a), math.sin(a)), r * 0.52))
            out.append(f"M{cx:.0f},{cy:.0f} L{f(e)}")
            out.append(circle(e[0], e[1], r * 0.055))
        out.append(circle(cx, cy, r * 0.11))
        return out

    p += blossom(430, 560, 128, 0.3)
    p += blossom(700, 452, 112, 1.0)
    p += blossom(255, 700, 96, -0.4)
    p += blossom(880, 640, 84, 0.8)
    for (bx, by, ba) in ((545, 545, -0.9), (820, 520, -0.4), (150, 800, -1.2)):
        p.append(pointed_petal((bx, by), ba + math.pi / 2, 66, 30, bulge=0.62))
        p.append(pointed_petal((bx, by), ba + math.pi / 2, 44, 20, bulge=0.62))
    return p


def lily():
    p = []
    cx, cy = HALF, 470
    for i in range(3):
        a = -math.pi / 2 + i * 2 * math.pi / 3
        p.append(pointed_petal((cx, cy), a, 330, 92, bulge=0.48))
    for i in range(3):
        a = -math.pi / 2 + (i + 0.5) * 2 * math.pi / 3
        p.append(pointed_petal((cx, cy), a, 305, 84, bulge=0.48))
    for i in range(3):                                   # one crease per front petal
        a = -math.pi / 2 + i * 2 * math.pi / 3
        p.append(vein((cx, cy), a, 215))
    for i in range(3):
        a = -math.pi / 2 + (i + 0.5) * 2 * math.pi / 3
        p.append(vein((cx, cy), a, 195))
    for i in range(6):                                   # stamens
        a = -math.pi / 2 + (i - 2.5) * math.radians(15)
        e = add((cx, cy), mul((math.cos(a), math.sin(a)), 172 + (i % 2) * 30))
        p.append(f"M{cx},{cy} Q{cx + (e[0] - cx) * 0.5:.0f},{cy + (e[1] - cy) * 0.62:.0f} {f(e)}")
        p.append(pointed_petal(e, a, 38, 12, bulge=0.5))
    p.append(circle(cx, cy, 15))
    p.append(stem(cx, cy + 210, cx + 6, 960, bend=-28))
    p.append(leaf((cx + 2, 730), math.radians(200), 260, 40, curl=0.26))
    p.append(leaf((cx + 6, 830), math.radians(-24), 240, 36, curl=-0.26))
    return p


def poppy():
    p = []
    cx, cy = HALF, 400
    for i in range(4):
        a = -math.pi / 2 + i * math.pi / 2 + math.pi / 4
        b = add((cx, cy), mul((math.cos(a), math.sin(a)), 40))
        L = 265
        p.append(petal(b, a, L, 175, waist=0.10, tip=0.55))
        d = (math.cos(a), math.sin(a))
        n = (-d[1], d[0])
        for k in (-1, 0, 1):                             # crinkles, kept inside the rim
            o = k * 52
            p.append(f"M{f(add(b, mul(d, 0.86 * L), mul(n, o - 26)))} "
                     f"Q{f(add(b, mul(d, 0.95 * L), mul(n, o)))} "
                     f"{f(add(b, mul(d, 0.86 * L), mul(n, o + 26)))}")
        p.append(vein(b, a, 0.62 * L))
        for k in (-1, 1):
            p.append(vein(b, a + k * 0.30, 0.50 * L, k * 0.08))
    p.append(circle(cx, cy, 60))
    p.append(circle(cx, cy, 38))
    for i in range(12):
        a = i * math.pi / 6
        p.append(f"M{cx + 38 * math.cos(a):.0f},{cy + 38 * math.sin(a):.0f} "
                 f"L{cx + 60 * math.cos(a):.0f},{cy + 60 * math.sin(a):.0f}")
    for i in range(14):                                  # stamen ring
        a = i * 2 * math.pi / 14 + 0.12
        e = add((cx, cy), mul((math.cos(a), math.sin(a)), 100))
        p.append(f"M{cx + 60 * math.cos(a):.0f},{cy + 60 * math.sin(a):.0f} L{f(e)}")
        p.append(circle(e[0], e[1], 7.5))
    p.append(stem(cx - 10, cy + 250, cx - 40, 960, bend=44))
    p.append(stem(cx + 60, cy + 210, cx + 210, 640, bend=70))
    p.append(pointed_petal((cx + 214, 648), math.radians(-72), 130, 62, bulge=0.62))
    p.append(vein((cx + 214, 648), math.radians(-72), 112))
    p.append(leaf((cx - 26, 760), math.radians(-28), 200, 40, curl=-0.34))
    p.append(leaf((cx - 34, 850), math.radians(206), 190, 38, curl=0.34))
    return p


def hibiscus():
    p = []
    cx, cy = HALF, 430
    for i in range(5):
        a = -math.pi / 2 + i * 2 * math.pi / 5
        b = add((cx, cy), mul((math.cos(a), math.sin(a)), 34))
        L = 285
        p.append(petal(b, a, L, 158, waist=0.12, tip=0.60))
        d = (math.cos(a), math.sin(a))
        n = (-d[1], d[0])
        for k in (-1, 0, 1):                            # gentle ruffled rim
            o = k * 44
            p.append(f"M{f(add(b, mul(d, 0.88 * L), mul(n, o - 22)))} "
                     f"Q{f(add(b, mul(d, 0.97 * L), mul(n, o)))} "
                     f"{f(add(b, mul(d, 0.88 * L), mul(n, o + 22)))}")
        p.append(vein(b, a, 0.66 * L))
        for k in (-1, 1):
            p.append(vein(b, a + k * 0.28, 0.54 * L, k * 0.08))
    col_a = math.radians(-62)                            # staminal column
    clen = 268
    tipc = add((cx, cy), mul(rot((1, 0), col_a), clen))
    for s in (-1, 1):
        off = mul(rot((1, 0), col_a + math.pi / 2), s * 13)
        p.append(f"M{f(add((cx, cy), off))} "
                 f"Q{f(add((cx, cy), mul(rot((1, 0), col_a), clen * 0.55), mul(off, 1.2)))} "
                 f"{f(add(tipc, mul(off, 0.7)))}")
    for i in range(10):                                  # anthers along the column
        s = 1 if i % 2 else -1
        base = add((cx, cy), mul(rot((1, 0), col_a), clen * (0.58 + 0.042 * i)))
        e = add(base, mul(rot((1, 0), col_a + s * math.radians(74)), 30 + (i % 3) * 9))
        p.append(f"M{f(base)} L{f(e)}")
        p.append(circle(e[0], e[1], 8))
    for i in range(5):                                   # five stigma pads at the tip
        a = col_a + (i - 2) * math.radians(15)
        e = add(tipc, mul(rot((1, 0), a), 34))
        p.append(f"M{f(tipc)} L{f(e)}")
        p.append(circle(e[0], e[1], 11))
    p.append(stem(cx, cy + 290, cx + 8, 960, bend=-30))
    p.append(leaf((cx + 2, 700), math.radians(206), 210, 78, curl=0.28))
    p.append(leaf((cx + 6, 812), math.radians(-26), 195, 72, curl=-0.28))
    p.append(vein((cx + 2, 700), math.radians(206), 186, 0.28))
    p.append(vein((cx + 6, 812), math.radians(-26), 172, -0.28))
    return p


FLOWERS = [
    ("rose", "Rose", rose),
    ("sunflower", "Sunflower", sunflower),
    ("tulip", "Tulip", tulip),
    ("daisy", "Daisy", daisy),
    ("lotus", "Lotus", lotus),
    ("cherry", "Cherry blossom", cherry_blossom),
    ("lily", "Lily", lily),
    ("poppy", "Poppy", poppy),
    ("hibiscus", "Hibiscus", hibiscus),
]


def to_svg(paths, sw=4.2):
    body = "".join(f'<path d="{d}"/>' for d in paths)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {W}" width="{W}" height="{W}">'
            f'<g fill="none" stroke="#111" stroke-width="{sw}" '
            f'stroke-linecap="round" stroke-linejoin="round">{body}</g></svg>')


out = {}
for key, label, fn in FLOWERS:
    svg = to_svg(fn())
    path = os.path.join(BUILD, f"flower_{key}.svg")
    open(path, "w").write(svg)
    out[key] = {"label": label, "svg": svg}
    print(f"{key:12s} {len(svg):6d} bytes")

json.dump(out, open(os.path.join(BUILD, "flowers.json"), "w"))
print("total", sum(len(v['svg']) for v in out.values()), "bytes")
