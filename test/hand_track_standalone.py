#!/usr/bin/env python3
"""Track the drawing hand across raw footage - no app involved.

Same principle the app uses: cancel the camera's own motion first, then
whatever still moves IS the hand. Per frame pair:
  1. estimate the dominant (camera) motion as a homography from LK optical
     flow + RANSAC,
  2. warp the previous frame onto the current one,
  3. absdiff -> the residual is only what moved relative to the scene,
  4. largest blob = hand; EMA-smoothed centroid drawn as a red dot + trail.

Bottom-left inset shows the residual motion mask, i.e. what the algorithm
actually sees.

Usage: .venv/bin/python test/hand_track_standalone.py footage.mov [out.mp4]
"""
import os, subprocess, sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    src = os.path.abspath(sys.argv[1])
    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "build", "hand_track.mp4")

    cap = cv2.VideoCapture(src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    ok, prev = cap.read()
    assert ok, "cannot read footage"
    W = 960
    scale = W / prev.shape[1]
    H = int(prev.shape[0] * scale)
    prev = cv2.resize(prev, (W, H))
    prev_g = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)

    raw = os.path.join(ROOT, "build", "_track_raw.mp4")
    out = cv2.VideoWriter(raw, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))

    ema = None
    trail = []
    recent = []
    frames = hits = 0
    while True:
        ok, cur = cap.read()
        if not ok:
            break
        cur = cv2.resize(cur, (W, H))
        cur_g = cv2.cvtColor(cur, cv2.COLOR_BGR2GRAY)
        frames += 1

        # 1. dominant motion = the camera
        pts = cv2.goodFeaturesToTrack(prev_g, 400, 0.01, 12)
        mask = None
        if pts is not None and len(pts) >= 12:
            nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev_g, cur_g, pts, None)
            good = st.reshape(-1) == 1
            if good.sum() >= 12:
                Hm, _ = cv2.findHomography(pts[good], nxt[good], cv2.RANSAC, 3.0)
                if Hm is not None:
                    # 2-3. cancel it, diff the rest
                    warped = cv2.warpPerspective(prev_g, Hm, (W, H))
                    diff = cv2.absdiff(cur_g, warped)
                    diff[warped == 0] = 0        # ignore borders the warp exposed
                    _, mask = cv2.threshold(diff, 22, 255, cv2.THRESH_BINARY)
                    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
                    mask = cv2.dilate(mask, np.ones((9, 9), np.uint8))

        # 4. largest moving blob = the hand
        target = None
        if mask is not None:
            n, lab, stats, cents = cv2.connectedComponentsWithStats(mask)
            cand = [(stats[i, cv2.CC_STAT_AREA], cents[i]) for i in range(1, n)
                    if stats[i, cv2.CC_STAT_AREA] > 250]
            cand.sort(key=lambda c: -c[0])
            cand = cand[:3]
            if cand:
                if ema is None:
                    target = cand[0][1]
                else:
                    # stickiness: of the biggest blobs, take the one nearest
                    # the previous position - shadows and freshly inked
                    # strokes flicker in elsewhere, the hand doesn't teleport
                    target = min(cand, key=lambda c: np.hypot(*(c[1] - ema)))[1]
        if target is not None:
            hits += 1
            recent.append(np.asarray(target))
            med = np.median(recent[-5:], axis=0)
            ema = med if ema is None else 0.78 * np.asarray(ema) + 0.22 * med
        if ema is not None:
            trail.append(tuple(int(v) for v in ema))

        vis = cur.copy()
        for a, b in zip(trail[-40:], trail[-39:]):
            cv2.line(vis, a, b, (80, 80, 255), 2)
        if ema is not None:
            p = tuple(int(v) for v in ema)
            cv2.circle(vis, p, 26, (0, 0, 255), 4)
            cv2.circle(vis, p, 5, (0, 0, 255), -1)
        if mask is not None:
            inset = cv2.resize(mask, (W // 4, H // 4))
            vis[H - H // 4:, :W // 4] = cv2.cvtColor(inset, cv2.COLOR_GRAY2BGR)
            cv2.rectangle(vis, (0, H - H // 4), (W // 4 - 1, H - 1), (255, 255, 255), 1)
        out.write(vis)
        prev_g = cur_g

    cap.release(); out.release()
    subprocess.run(["ffmpeg", "-y", "-i", raw, "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-crf", "23", out_path], check=True, capture_output=True)
    os.remove(raw)
    print(f"frames: {frames}, moving-blob target found: {hits} ({100 * hits / frames:.0f}%)")
    print("video:", out_path)


if __name__ == "__main__":
    main()
