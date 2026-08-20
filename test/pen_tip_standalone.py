#!/usr/bin/env python3
"""Track the PEN TIP across raw footage - no app involved.

Physically grounded: fresh ink appears exactly at the nib. The printed
sheet's own markers give an exact sheet-anchored coordinate frame per
frame (OpenCV ArUco with our custom dictionary), so:

  1. detect markers -> homography image -> sheet millimetres,
  2. warp each frame into the fixed sheet frame (camera motion is gone),
  3. pixels that were paper ~0.5s ago and are near-black now = new ink;
     keep only THIN structures (strokes are 1-3mm; hands and shadows are
     huge and get removed by the opening),
  4. the newest ink cluster = the nib. Held while the pen hovers.

Inset bottom-left: the fresh-ink mask. Circle = tracked nib.

Usage: .venv/bin/python test/pen_tip_standalone.py footage.mov [out.mp4]
"""
import collections, json, os, subprocess, sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# sheet geometry, millimetres (gen_canvas.py)
FRAME_W, FRAME_H = 101.6, 152.4
Q, MARKER, GAP = 4.8, 20.0, 5.0
STICKER = MARKER + 2 * Q
OFF = GAP + STICKER
# sticker lower-left origins in y-UP coords relative to the frame's lower-left
ORIGINS = [(-OFF, FRAME_H + GAP), (FRAME_W + GAP, FRAME_H + GAP),
           (-OFF, -OFF), (FRAME_W + GAP, -OFF),
           (FRAME_W / 2 - STICKER / 2, FRAME_H + GAP),
           (FRAME_W / 2 - STICKER / 2, -OFF),
           (-OFF, FRAME_H / 2 - STICKER / 2), (FRAME_W + GAP, FRAME_H / 2 - STICKER / 2)]
S = 3.0                                  # sheet-canvas px per mm
X0, Y0 = -40.0, -40.0                    # sheet-canvas origin, mm
CW = int((FRAME_W + 80) * S)
CH = int((FRAME_H + 80) * S)


def sheet_corners(i):
    """Marker i's black-square corners [TL,TR,BR,BL] in y-down sheet mm."""
    ox, oy = ORIGINS[i]
    ll = (ox + Q, oy + Q)
    yup = [(ll[0], ll[1] + MARKER), (ll[0] + MARKER, ll[1] + MARKER),
           (ll[0] + MARKER, ll[1]), ll]
    return [(x, FRAME_H - y) for x, y in yup]


def make_dictionary():
    d = json.load(open(os.path.join(ROOT, "build", "dictionary.json")))
    n = d["gridSize"]
    bl = []
    for code in d["codes"][:8]:
        bits = np.array([[(code >> (n * n - 1 - (r * n + c))) & 1
                          for c in range(n)] for r in range(n)], dtype=np.uint8)
        bl.append(cv2.aruco.Dictionary.getByteListFromBits(bits))
    return cv2.aruco.Dictionary(np.concatenate(bl), n, d["maxCorrectableBits"])


def main():
    src = os.path.abspath(sys.argv[1])
    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "build", "pen_tip.mp4")

    det = cv2.aruco.ArucoDetector(make_dictionary(), cv2.aruco.DetectorParameters())
    to_px = lambda pts: np.array([[(x - X0) * S, (y - Y0) * S] for x, y in pts], np.float32)

    cap = cv2.VideoCapture(src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    W = 960
    ok, fr = cap.read()
    assert ok
    scale = W / fr.shape[1]
    H = int(fr.shape[0] * scale)
    raw = os.path.join(ROOT, "build", "_tip_raw.mp4")
    out = cv2.VideoWriter(raw, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    LAG = 15                             # compare against ~0.5s ago
    hist = collections.deque(maxlen=LAG + 1)
    tip = None                           # sheet-canvas px
    tip_seen = 0
    frames = det_frames = ink_frames = 0
    trail = []

    while True:
        ok, fr = cap.read()
        if not ok:
            break
        fr = cv2.resize(fr, (W, H))
        gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        frames += 1

        corners, ids, _ = det.detectMarkers(gray)
        Hm = None
        if ids is not None and len(ids) >= 1:
            src_pts, dst_pts = [], []
            for c, i in zip(corners, ids.reshape(-1)):
                if i < 8:
                    src_pts.extend(c.reshape(4, 2))
                    dst_pts.extend(to_px(sheet_corners(int(i))))
            if len(src_pts) >= 4:
                Hm, _ = cv2.findHomography(np.array(src_pts), np.array(dst_pts), cv2.RANSAC, 4.0)
        if Hm is not None:
            det_frames += 1
            warp = cv2.warpPerspective(gray, Hm, (CW, CH), borderValue=0)
            valid = cv2.warpPerspective(np.full_like(gray, 255), Hm, (CW, CH)) > 200
            hist.append((warp, valid))

            mask = None
            if len(hist) > LAG:
                ref, rvalid = hist[0]
                both = valid & rvalid
                new_dark = ((ref > 150) & (warp < 110) & both).astype(np.uint8) * 255
                # strokes are thin; hands/shadows are wide - opening keeps the wide
                wide = cv2.morphologyEx(new_dark, cv2.MORPH_OPEN, np.ones((13, 13), np.uint8))
                thin = cv2.subtract(new_dark, wide)
                thin = cv2.morphologyEx(thin, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
                if np.count_nonzero(thin) < 0.05 * thin.size:   # AE flicker guard
                    mask = cv2.dilate(thin, np.ones((7, 7), np.uint8))

            if mask is not None:
                n, lab, stats, cents = cv2.connectedComponentsWithStats(mask)
                cand = [(stats[i, cv2.CC_STAT_AREA], cents[i]) for i in range(1, n)
                        if stats[i, cv2.CC_STAT_AREA] > 40]
                if cand:
                    ink_frames += 1
                    if tip is None:
                        best = max(cand, key=lambda c: c[0])[1]
                    else:
                        best = min(cand, key=lambda c: np.hypot(*(c[1] - tip)))[1]
                    tip = best if tip is None else 0.6 * np.asarray(tip) + 0.4 * np.asarray(best)
                    tip_seen = frames

        vis = fr.copy()
        if tip is not None and Hm is not None and frames - tip_seen < 2 * fps:
            # map the sheet-space tip back onto the raw frame
            inv = np.linalg.inv(Hm)
            p = inv @ np.array([tip[0], tip[1], 1.0])
            p = (int(p[0] / p[2]), int(p[1] / p[2]))
            trail.append(p)
            for a, b in zip(trail[-40:], trail[-39:]):
                cv2.line(vis, a, b, (80, 80, 255), 2)
            cv2.circle(vis, p, 24, (0, 0, 255), 4)
            cv2.circle(vis, p, 4, (0, 0, 255), -1)
        if Hm is not None and len(hist) > LAG:
            inset_src = mask if mask is not None else np.zeros((CH, CW), np.uint8)
            inset = cv2.resize(inset_src, (W // 5, int(W // 5 * CH / CW)))
            ih, iw = inset.shape
            vis[H - ih:, :iw] = cv2.cvtColor(inset, cv2.COLOR_GRAY2BGR)
            cv2.rectangle(vis, (0, H - ih), (iw - 1, H - 1), (255, 255, 255), 1)
        out.write(vis)

    cap.release(); out.release()
    subprocess.run(["ffmpeg", "-y", "-i", raw, "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-crf", "23", out_path], check=True, capture_output=True)
    os.remove(raw)
    print(f"frames: {frames}, sheet locked: {det_frames}"
          f" ({100 * det_frames / frames:.0f}%), fresh-ink fixes: {ink_frames}")
    print("video:", out_path)


if __name__ == "__main__":
    main()
