#!/usr/bin/env python3
"""Prove real hand tracking works on the user's own footage BEFORE building
it into the app: run MediaPipe HandLandmarker over every frame, report the
detection rate and fingertip stability, and write an annotated video
(green skeleton dot = index fingertip, i.e. the point follow would centre).

Usage: .venv/bin/python test/hand_proof.py path/to/footage.mov
"""
import math, os, subprocess, sys, urllib.request

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MODEL = os.path.join(HERE, "hand_landmarker.task")
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/latest/hand_landmarker.task")


def main():
    src = os.path.abspath(sys.argv[1])
    if not os.path.exists(MODEL):
        print("downloading hand_landmarker.task...")
        urllib.request.urlretrieve(MODEL_URL, MODEL)

    lm = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=MODEL),
        running_mode=vision.RunningMode.VIDEO, num_hands=1,
        min_hand_detection_confidence=0.3, min_tracking_confidence=0.3))

    cap = cv2.VideoCapture(src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    raw = os.path.join(ROOT, "build", "_hand_raw.mp4")
    out = cv2.VideoWriter(raw, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))

    frames = hits = 0
    tips = []
    while True:
        ok, bgr = cap.read()
        if not ok:
            break
        frames += 1
        img = mp.Image(image_format=mp.ImageFormat.SRGB,
                       data=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        res = lm.detect_for_video(img, int(frames * 1000 / fps))
        if res.hand_landmarks:
            hits += 1
            pts = res.hand_landmarks[0]
            for p in pts:
                cv2.circle(bgr, (int(p.x * w), int(p.y * h)), 6, (0, 200, 255), -1)
            tip = pts[8]  # index fingertip = the follow target
            tx, ty = int(tip.x * w), int(tip.y * h)
            tips.append((frames / fps, tx, ty))
            cv2.circle(bgr, (tx, ty), 22, (0, 255, 0), 5)
        else:
            cv2.putText(bgr, "NO HAND", (40, 90),
                        cv2.FONT_HERSHEY_SIMPLEX, 2.2, (0, 0, 255), 5)
        out.write(bgr)
    cap.release(); out.release()

    final = os.path.join(ROOT, "build", "hand_proof.mp4")
    subprocess.run(["ffmpeg", "-y", "-i", raw, "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-crf", "23", "-vf", "scale=720:-2",
                    final], check=True, capture_output=True)
    os.remove(raw)

    jumps = [math.hypot(b[1] - a[1], b[2] - a[2])
             for a, b in zip(tips, tips[1:]) if b[0] - a[0] < 0.2]
    print(f"frames: {frames}, hand detected: {hits} ({100 * hits / frames:.0f}%)")
    if jumps:
        jumps.sort()
        print(f"fingertip inter-frame jump px: median {jumps[len(jumps) // 2]:.0f},"
              f" p95 {jumps[int(len(jumps) * .95)]:.0f} (frame {w}x{h})")
    print("video:", final)


if __name__ == "__main__":
    main()
