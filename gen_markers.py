#!/usr/bin/env python3
"""Generate a 4x4 fiducial dictionary with large inter-marker Hamming distance
(across all 4 rotations), render marker images, and build printable sticker sheets."""
import json, random, itertools, os
import numpy as np
from PIL import Image, ImageDraw

N = 4                      # inner grid is N x N
BITS = N * N
OUT = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(OUT, "build")
os.makedirs(BUILD, exist_ok=True)


def bits_to_grid(code):
    return np.array([[(code >> (BITS - 1 - (r * N + c))) & 1 for c in range(N)]
                     for r in range(N)], dtype=np.uint8)


def grid_to_bits(g):
    v = 0
    for r in range(N):
        for c in range(N):
            v = (v << 1) | int(g[r, c])
    return v


def rotations(code):
    g = bits_to_grid(code)
    out = []
    for _ in range(4):
        out.append(grid_to_bits(g))
        g = np.rot90(g, -1)          # rotate 90 deg clockwise
    return out


def hamming(a, b):
    return bin(a ^ b).count("1")


def self_rot_distance(code):
    rots = rotations(code)
    return min(hamming(rots[0], rots[i]) for i in range(1, 4))


def transitions(code):
    """Count 4-neighbour colour changes inside the grid; higher = more texture."""
    g = bits_to_grid(code)
    t = int(np.sum(g[:, :-1] != g[:, 1:])) + int(np.sum(g[:-1, :] != g[1:, :]))
    return t


def build_dictionary(want=24, tau=8, seed=7):
    rng = random.Random(seed)
    # Candidate pool: balanced-ish bit counts, good self-rotation distance, textured.
    cands = []
    for code in range(1 << BITS):
        ones = bin(code).count("1")
        if not (6 <= ones <= 10):
            continue
        if self_rot_distance(code) < tau:
            continue
        if transitions(code) < 12:
            continue
        cands.append(code)
    rng.shuffle(cands)

    best = []
    for attempt in range(400):
        rng.shuffle(cands)
        chosen, chosen_rots = [], []
        for code in cands:
            ok = True
            for rots in chosen_rots:
                if min(hamming(code, r) for r in rots) < tau:
                    ok = False
                    break
            if ok:
                chosen.append(code)
                chosen_rots.append(rotations(code))
                if len(chosen) >= want:
                    break
        if len(chosen) > len(best):
            best = chosen
        if len(best) >= want:
            break
    return sorted(best)


def verify(codes, tau):
    allrots = {c: rotations(c) for c in codes}
    mind = 99
    for a, b in itertools.combinations(codes, 2):
        d = min(hamming(a, r) for r in allrots[b])
        mind = min(mind, d)
    minself = min(self_rot_distance(c) for c in codes)
    return mind, minself


TAU = 8
codes = build_dictionary(want=24, tau=TAU)
if len(codes) < 12:
    TAU = 7
    codes = build_dictionary(want=24, tau=TAU)
if len(codes) < 12:
    TAU = 6
    codes = build_dictionary(want=24, tau=TAU)

mind, minself = verify(codes, TAU)
maxcorrect = (min(mind, minself) - 1) // 2
print(f"dictionary: {len(codes)} markers, tau={TAU}, "
      f"min inter-marker distance={mind}, min self-rotation distance={minself}, "
      f"correctable bits={maxcorrect}")

# ---------------------------------------------------------------- rendering
MODULES = N + 2            # 1-module black border all around


def marker_matrix(code):
    m = np.zeros((MODULES, MODULES), dtype=np.uint8)   # 0 = black
    m[1:-1, 1:-1] = bits_to_grid(code)                 # 1 = white
    return m


def marker_image(code, module_px=24, quiet_modules=1):
    m = marker_matrix(code)
    size = (MODULES + 2 * quiet_modules) * module_px
    img = Image.new("L", (size, size), 255)
    d = ImageDraw.Draw(img)
    for r in range(MODULES):
        for c in range(MODULES):
            if m[r, c] == 0:
                x0 = (c + quiet_modules) * module_px
                y0 = (r + quiet_modules) * module_px
                d.rectangle([x0, y0, x0 + module_px - 1, y0 + module_px - 1], fill=0)
    return img


for i, code in enumerate(codes):
    marker_image(code).save(os.path.join(BUILD, f"marker_{i:02d}.png"))

meta = {
    "gridSize": N,
    "modules": MODULES,
    "tau": TAU,
    "minDistance": int(mind),
    "minSelfRotation": int(minself),
    "maxCorrectableBits": int(maxcorrect),
    "codes": [int(c) for c in codes],
    "rotations": {str(i): [int(x) for x in rotations(c)] for i, c in enumerate(codes)},
}
with open(os.path.join(BUILD, "dictionary.json"), "w") as f:
    json.dump(meta, f, indent=1)
print("wrote", len(codes), "marker PNGs +ic dictionary.json")
