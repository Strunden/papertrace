/* Tests for MarkerMap: does a scattered set of stickers get chained into one
 * consistent paper frame, and does a single visible sticker then hold the pose?
 *
 * Note on tolerances: recovering a plane pose from ONE square is intrinsically
 * noisy - 4 points is exactly 8 DOF, so corner noise feeds straight into the
 * perspective terms and is amplified when you extrapolate away from the tag.
 * Several tests therefore compare the learnt map against a "perfect map"
 * baseline measured in the same run, rather than against an absolute number
 * that no implementation could reach.
 */

function worldCorners(m) {
  const half = m.size / 2;
  const ct = Math.cos(m.angle), st = Math.sin(m.angle);
  return [[-half, -half], [half, -half], [half, half], [-half, half]]
    .map(([lx, ly]) => [m.cx + lx * ct - ly * st, m.cy + lx * st + ly * ct]);
}

function cameraAt(w, h, t) {
  // A wide hand-held sweep: pans right, drifts, tilts and rolls along the way.
  const camX = -260 + t * 700;
  const camY = -60 + 190 * Math.sin(t * Math.PI * 1.3);
  const view = viewHomography(w, h,
    0.0004 * Math.sin(t * 5.0), 0.0005 * Math.cos(t * 3.7),
    1.05 + 0.25 * Math.sin(t * 2.1), 0, 0,
    0.35 * Math.sin(t * 2.6));
  return matMul(view, new Float64Array([1, 0, -camX, 0, 1, -camY, 0, 0, 1]));
}

/** Phone held over one sheet of paper: small drifts, most tags stay in frame. */
function deskCameraAt(w, h, t) {
  const a = t * Math.PI * 2;
  const view = viewHomography(w, h,
    0.00035 * Math.sin(a * 1.7), 0.00040 * Math.cos(a * 1.3),
    1.42 + 0.14 * Math.sin(a * 0.9), 0, 0,
    0.22 * Math.sin(a * 1.1));
  const camX = 16 * Math.sin(a * 1.9), camY = 13 * Math.cos(a * 1.4);
  return matMul(view, new Float64Array([1, 0, -camX, 0, 1, -camY, 0, 0, 1]));
}

function makeRng(s0) {
  let s = s0;
  return () => { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; };
}

function runTrackerTests(dict) {
  const results = [];
  const log = (name, pass, detail) => results.push({ name, pass, detail });
  const W = 640, H = 480;

  // A wide, awkward layout: tags far apart, mixed sizes, rarely all in frame.
  const SPREAD = [
    { id: 0, cx: 0, cy: 0, size: 90, angle: 0.1 },
    { id: 1, cx: 220, cy: -40, size: 90, angle: 0.7 },
    { id: 2, cx: 430, cy: 60, size: 60, angle: -0.5 },
    { id: 3, cx: 150, cy: 150, size: 120, angle: 2.0 },
    { id: 4, cx: -190, cy: 190, size: 90, angle: 1.1 },
    { id: 5, cx: -260, cy: -130, size: 46, angle: -1.4 },
  ];
  // Realistic: 30mm tags around an A4 sheet, ~1.9 px/mm.
  const DESK = [
    { id: 6, cx: -128, cy: -88, size: 30, angle: 0.05 },
    { id: 7, cx: 128, cy: -90, size: 30, angle: -0.62 },
    { id: 8, cx: -130, cy: 88, size: 30, angle: 1.9 },
    { id: 9, cx: 126, cy: 90, size: 30, angle: 2.7 },
    { id: 10, cx: 4, cy: -96, size: 30, angle: 0.9 },
  ];

  const makeScene = (markers, camFn) => {
    const quads = new Map(markers.map((m) => [m.id, worldCorners(m)]));
    const visibleAt = (Ht, noise, rng) => {
      const out = [];
      for (const m of markers) {
        const proj = quads.get(m.id).map(([x, y]) => matApply(Ht, x, y));
        if (!proj.every(([x, y]) => x > 8 && y > 8 && x < W - 8 && y < H - 8)) continue;
        let minSide = Infinity;
        for (let i = 0; i < 4; i++) {
          const a = proj[i], b = proj[(i + 1) % 4];
          minSide = Math.min(minSide, Math.hypot(b[0] - a[0], b[1] - a[1]));
        }
        if (minSide < 16) continue;                     // too small to decode
        out.push({ id: m.id, corners: proj.map(([x, y]) =>
          [x + (rng() * 2 - 1) * noise, y + (rng() * 2 - 1) * noise]) });
      }
      return out;
    };
    return { quads, visibleAt, camFn };
  };

  /**
   * Run a sweep and measure, per frame, both the learnt-map pose error and the
   * error a perfect map would have given on the very same detections.
   */
  const sweep = (scene, markers, opts) => {
    const { steps = 90, noise = 0.15, occludeAfter = null, seed = 12345 } = opts || {};
    const rng = makeRng(seed);
    const mm = new MarkerMap();
    let paperToWorld = null;
    const learnt = [], ideal = [], single = [], smooth = [];
    let tracked = 0, missed = 0;
    const probes = [[0, 0], [1.6, -0.9], [-1.1, 1.4]];

    for (let s = 0; s < steps; s++) {
      const t = s / (steps - 1);
      const Ht = scene.camFn(W, H, t);
      let dets = scene.visibleAt(Ht, noise, rng);
      if (occludeAfter !== null && t > occludeAfter && dets.length > 1) {
        dets = [dets[s % dets.length]];                 // a hand covering all but one
      }
      const hadKnown = dets.some((d) => mm.map.has(d.id));
      const res = mm.update(dets);
      // Holding a pose (e.g. a rejected spike) still gives the user a stable
      // overlay, so it only counts as a miss if nothing at all comes out.
      if (!res.tracking) { if (hadKnown && !res.H) missed++; continue; }
      tracked++;

      if (!paperToWorld) {
        paperToWorld = solveHomography(mm.cornersOf(mm.seedId), scene.quads.get(mm.seedId));
      }
      // What a perfect map would have produced from these same detections.
      const src = [], dst = [];
      for (const d of dets) {
        if (!mm.map.has(d.id)) continue;
        const c = scene.quads.get(d.id);
        for (let i = 0; i < 4; i++) { src.push(c[i]); dst.push(d.corners[i]); }
      }
      // Must include single-tag frames too, or the baseline quietly measures
      // only the easy frames and every comparison against it is meaningless.
      const Hideal = src.length >= 4 ? solveHomography(src, dst) : null;

      for (const p of probes) {
        const wp = matApply(paperToWorld, p[0], p[1]);
        const expect = matApply(Ht, wp[0], wp[1]);
        const got = matApply(res.rawH, p[0], p[1]);
        const e = Math.hypot(got[0] - expect[0], got[1] - expect[1]);
        learnt.push(e);
        if (res.known === 1) single.push(e);
        const gs = matApply(res.H, p[0], p[1]);
        smooth.push(Math.hypot(gs[0] - expect[0], gs[1] - expect[1]));
        if (Hideal) {
          const gi = matApply(Hideal, wp[0], wp[1]);
          ideal.push(Math.hypot(gi[0] - expect[0], gi[1] - expect[1]));
        }
      }
    }
    const stat = (a) => a.length
      ? { mean: a.reduce((x, y) => x + y, 0) / a.length, max: Math.max(...a), n: a.length }
      : { mean: 0, max: 0, n: 0 };
    return { mm, tracked, missed, learnt: stat(learnt), ideal: stat(ideal),
             single: stat(single), smooth: stat(smooth) };
  };

  /* ------------------------------------------------- realistic desk layout */
  {
    const scene = makeScene(DESK, deskCameraAt);
    const r = sweep(scene, DESK, { steps: 80, noise: 0.2 });
    log('desk layout: every tag registered', r.mm.size === DESK.length,
        `registered ${r.mm.size}/${DESK.length}`);
    log('desk layout: pose accurate', r.learnt.mean < 0.6 && r.learnt.max < 2.0,
        `mean ${r.learnt.mean.toFixed(2)}px, max ${r.learnt.max.toFixed(2)}px`);
    log('desk layout: map error is sub-pixel', r.learnt.mean < 0.8 && r.learnt.max < 2.0,
        `learnt ${r.learnt.mean.toFixed(3)}px (perfect map on the same frames: ${r.ideal.mean.toFixed(3)}px)`);
    log('desk layout: never dropped a visible known tag', r.missed === 0,
        `${r.tracked} tracked, ${r.missed} missed`);
  }

  /* ------------------------------------------- wide sweep, chained tags --- */
  {
    const scene = makeScene(SPREAD, cameraAt);
    const r = sweep(scene, SPREAD, { steps: 90, noise: 0.15 });
    log('wide sweep: chains tags across the workspace', r.mm.size >= 5,
        `registered ${r.mm.size}/${SPREAD.length}`);
    // 640px-wide detection frame, so 2.5px is well under half a percent of the
    // view - a fraction of a millimetre on an A4 sheet.
    log('wide sweep: stays accurate while chaining', r.learnt.mean < 2.5 && r.learnt.max < 12,
        `mean ${r.learnt.mean.toFixed(2)}px, max ${r.learnt.max.toFixed(2)}px `
        + `(perfect map on the same frames: mean ${r.ideal.mean.toFixed(2)}px, max ${r.ideal.max.toFixed(2)}px)`);
    log('wide sweep: single visible tag still holds pose', r.single.n > 0 && r.single.mean < 3.5,
        `${r.single.n} single-tag probes, mean ${r.single.mean.toFixed(2)}px, max ${r.single.max.toFixed(2)}px`);
    log('wide sweep: never dropped a visible known tag', r.missed === 0,
        `${r.tracked} tracked, ${r.missed} missed`);
  }

  /* ------------------------------------------------ occlusion + noise ----- */
  {
    const scene = makeScene(DESK, deskCameraAt);
    const r = sweep(scene, DESK, { steps: 100, noise: 0.6, occludeAfter: 0.5, seed: 999 });
    log('occlusion: keeps tracking on one tag', r.missed === 0 && r.single.n > 0,
        `${r.tracked} tracked, ${r.missed} missed, ${r.single.n} single-tag probes`);
    log('occlusion: no worse than the single-tag limit',
        r.learnt.mean < r.ideal.mean * 1.5 + 0.5 && r.learnt.max < r.ideal.max * 1.5 + 2,
        `mean ${r.learnt.mean.toFixed(2)}px / max ${r.learnt.max.toFixed(2)}px vs `
        + `perfect map on the same frames mean ${r.ideal.mean.toFixed(2)}px / max ${r.ideal.max.toFixed(2)}px`);
  }

  /* --------------------------------------------- jitter with a still phone */
  {
    const scene = makeScene(DESK, deskCameraAt);
    const rng = makeRng(4242);
    const mm = new MarkerMap();
    const Ht = deskCameraAt(W, H, 0.42);
    for (let i = 0; i < 30; i++) mm.update(scene.visibleAt(Ht, 0.25, rng));   // learn the map
    const raw = [], smoothed = [];
    for (let i = 0; i < 60; i++) {
      const res = mm.update(scene.visibleAt(Ht, 0.25, rng));
      if (!res.tracking) continue;
      raw.push(matApply(res.rawH, 1.6, -0.9));
      smoothed.push(matApply(res.H, 1.6, -0.9));
    }
    const jitter = (a) => {
      let d = 0;
      for (let i = 1; i < a.length; i++) d += Math.hypot(a[i][0] - a[i - 1][0], a[i][1] - a[i - 1][1]);
      return d / Math.max(1, a.length - 1);
    };
    const jr = jitter(raw), js = jitter(smoothed);
    log('still phone: smoothing cuts jitter', js < jr * 0.75,
        `raw ${jr.toFixed(3)}px/frame -> smoothed ${js.toFixed(3)}px/frame`);
  }

  /* ------------------------------------------------------ edge cases ----- */
  {
    const scene = makeScene(DESK, deskCameraAt);
    const mm = new MarkerMap();
    const rng = makeRng(7);
    const Ht = deskCameraAt(W, H, 0.35);
    for (let i = 0; i < 5; i++) mm.update(scene.visibleAt(Ht, 0.1, rng));
    let heldFor = 0, finallyLost = false;
    for (let i = 0; i < 25; i++) {
      if (mm.update([]).H) heldFor++; else { finallyLost = true; break; }
    }
    log('hold-then-lose when tags disappear', heldFor >= 8 && heldFor <= 12 && finallyLost,
        `held ${heldFor} frames, then lost=${finallyLost}`);
  }
  {
    const scene = makeScene(DESK, deskCameraAt);
    const mm = new MarkerMap();
    mm.update(scene.visibleAt(deskCameraAt(W, H, 0.2), 0.1, makeRng(3)));
    const before = mm.size;
    mm.update([{ id: 17, corners: [[100, 100], [180, 104], [176, 184], [96, 180]] }]);
    log('never registers an unanchored tag', mm.size === before, `size ${before} -> ${mm.size}`);
  }
  {
    const mm = new MarkerMap();
    mm.update([]);
    const res = mm.update([]);
    log('empty input is harmless', res.tracking === false && res.H === null, 'no throw, no pose');
  }

  /* ------------------------------------------- end-to-end through pixels -- */
  {
    const det = new MarkerDetector(dict);
    const mm = new MarkerMap();
    const quads = new Map(DESK.map((m) => [m.id, worldCorners(m)]));
    let ok = 0, frames = 0, worst = 0, paperToWorld = null;
    for (let i = 0; i < 14; i++) {
      const t = 0.15 + i * 0.035;
      const Ht = deskCameraAt(W, H, t);
      const gray = renderScene({ w: W, h: H, H: Ht, markers: DESK, dict, blur: 1.0, noise: 3 });
      const res = mm.update(det.detect(gray, W, H));
      frames++;
      if (!res.tracking) continue;
      ok++;
      if (!paperToWorld) paperToWorld = solveHomography(mm.cornersOf(mm.seedId), quads.get(mm.seedId));
      const wp = matApply(paperToWorld, 1.2, 0.8);
      const expect = matApply(Ht, wp[0], wp[1]);
      const got = matApply(res.rawH, 1.2, 0.8);
      worst = Math.max(worst, Math.hypot(got[0] - expect[0], got[1] - expect[1]));
    }
    log('end-to-end: pixels -> detector -> map', ok === frames && worst < 2.0,
        `${ok}/${frames} frames tracked, worst ${worst.toFixed(2)}px, map ${mm.size}/${DESK.length}`);
  }

  return results;
}
