/* ---------------------------------------------------------------------------
 * tracker.js - turns per-frame marker detections into a stable paper pose.
 *
 * Stickers can be placed anywhere. The first one seen defines the paper
 * coordinate frame; every other sticker is registered into that frame from
 * frames where it is seen alongside already-known ones. After a sweep, ONE
 * visible sticker is enough to recover the full pose - which is what lets you
 * draw with a hand-held phone while your hand covers most of the tags.
 *
 * Two things make the map accurate enough to be usable:
 *
 *  1. Every tag is known to be a perfect square on the paper, so a raw
 *     back-projection is replaced by the best-fit square (similarity fit).
 *     That throws away 4 degrees of freedom of pure noise.
 *  2. A new tag is not committed from a single frame. Estimates are averaged
 *     over several frames from different viewpoints, and only committed once
 *     they agree - extrapolating a homography from one small square is very
 *     noise-sensitive, and averaging across viewpoints cancels most of it.
 * ------------------------------------------------------------------------- */

const REF_SQUARE = [[-1, -1], [1, -1], [1, 1], [-1, 1]];
const UNIT_SQUARE = [[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]];

class MarkerMap {
  constructor(opts = {}) {
    this.maxReproj = opts.maxReproj ?? 3.0;        // px, at detection resolution
    this.holdFrames = opts.holdFrames ?? 10;
    this.minRegisterSide = opts.minRegisterSide ?? 26;
    this.convergedWeight = opts.convergedWeight ?? 12;
    this.minSamples = opts.minSamples ?? 2;
    this.maxBad = opts.maxBad ?? 12;
    this.maxKeyframes = opts.maxKeyframes ?? 40;
    this.keyframesPerView = opts.keyframesPerView ?? 8;
    this.reset();
  }

  reset() {
    this.map = new Map();      // id -> {c:[x,y], s, cos, sin, obs, seed}
    this.pending = new Map();  // id -> running similarity-average
    this.keyframes = [];       // multi-tag views kept for global refinement
    this.sinceRefine = 0;
    this.smoothed = null;      // EMA of REF_SQUARE projected into image space
    this.lastH = null;
    this.lost = Infinity;
    this.quality = 0;
    this.seedId = null;
    this.lastProj = null;
    this.motion = 0;
    this.rejects = 0;
  }

  get size() { return this.map.size; }

  /**
   * The map lives in paper units and is resolution-independent, but the pose,
   * the smoothing state and the stored keyframe observations are all in
   * detection-image pixels. When the detector changes resolution to keep inside
   * its frame budget, they have to come with it - otherwise the overlay jumps
   * by the ratio between the two resolutions.
   */
  rescaleImageSpace(k) {
    if (!(k > 0) || Math.abs(k - 1) < 1e-9) return;
    const scalePts = (pts) => pts && pts.map((p) => [p[0] * k, p[1] * k]);
    this.smoothed = scalePts(this.smoothed);
    this.lastProj = scalePts(this.lastProj);
    if (this.lastH) this.lastH = matMul(new Float64Array([k, 0, 0, 0, k, 0, 0, 0, 1]), this.lastH);
    for (const kf of this.keyframes) {
      kf.proj = scalePts(kf.proj);
      for (const o of kf.obs) o.corners = scalePts(o.corners);
    }
    for (const [, e] of this.map) e.lastView = scalePts(e.lastView);
    for (const [, p] of this.pending) p.lastView = scalePts(p.lastView);
    this.motion *= k;
  }

  /** Corners of a mapped tag, in paper units. */
  static corners(e) {
    return UNIT_SQUARE.map(([ux, uy]) => [
      e.c[0] + e.cos * ux - e.sin * uy,
      e.c[1] + e.sin * ux + e.cos * uy,
    ]);
  }

  cornersOf(id) {
    const e = this.map.get(id);
    return e ? MarkerMap.corners(e) : null;
  }

  /**
   * Best-fit similarity (centre, rotation, uniform scale) taking the unit
   * square onto `quad`. Closed-form Procrustes.
   */
  static fitSquare(quad) {
    let cx = 0, cy = 0;
    for (const p of quad) { cx += p[0]; cy += p[1]; }
    cx /= 4; cy /= 4;
    let a = 0, b = 0;
    for (let i = 0; i < 4; i++) {
      const ux = UNIT_SQUARE[i][0], uy = UNIT_SQUARE[i][1];
      const px = quad[i][0] - cx, py = quad[i][1] - cy;
      a += ux * px + uy * py;         // sum u . p
      b += ux * py - uy * px;         // sum u x p
    }
    // sum |u|^2 = 4 * 0.5 = 2
    const cos = a / 2, sin = b / 2;
    const s = Math.hypot(cos, sin);
    if (!(s > 1e-6) || !isFinite(s)) return null;
    const fit = { c: [cx, cy], s, cos, sin };
    // Residual tells us whether the back-projection really was square.
    let resid = 0;
    const fitted = MarkerMap.corners(fit);
    for (let i = 0; i < 4; i++) {
      resid = Math.max(resid, Math.hypot(fitted[i][0] - quad[i][0], fitted[i][1] - quad[i][1]));
    }
    fit.resid = resid / s;
    return fit;
  }

  static minSide(corners) {
    let m = Infinity;
    for (let i = 0; i < 4; i++) {
      const a = corners[i], b = corners[(i + 1) % 4];
      m = Math.min(m, Math.hypot(b[0] - a[0], b[1] - a[1]));
    }
    return m;
  }

  /** Least-squares pose from every known marker in view, with outlier rejection. */
  _solvePose(known) {
    let pool = known.slice();
    for (let pass = 0; pass < 3; pass++) {
      const src = [], dst = [];
      for (const k of pool) {
        const c = MarkerMap.corners(this.map.get(k.id));
        for (let i = 0; i < 4; i++) { src.push(c[i]); dst.push(k.corners[i]); }
      }
      const H = solveHomography(src, dst);
      if (!H) return null;
      const err = reprojError(H, src, dst);
      // Guard the single-marker short circuit: a degenerate quad yields a
      // singular H and a NaN error, and NaN latches into the EMA for good.
      if (!isFinite(err)) return null;
      if (pool.length === 1 || err <= this.maxReproj) return { H, err, pool };

      let worst = null, worstErr = -1;
      for (const k of pool) {
        const c = MarkerMap.corners(this.map.get(k.id));
        const e = reprojError(H, c, k.corners);
        if (e > worstErr) { worstErr = e; worst = k; }
      }
      if (!worst || pool.length <= 1) return { H, err, pool };
      const we = this.map.get(worst.id);
      if (we && !we.seed) we.bad = (we.bad || 0) + 1;
      pool = pool.filter((k) => k !== worst);
    }
    return null;
  }

  _accumulate(id, fit, weight) {
    let p = this.pending.get(id);
    if (!p) { p = { n: 0, w: 0, cx: 0, cy: 0, co: 0, si: 0, cx2: 0, cy2: 0, s2: 0 }; this.pending.set(id, p); }
    p.n++; p.w += weight;
    p.cx += weight * fit.c[0]; p.cy += weight * fit.c[1];
    p.co += weight * fit.cos; p.si += weight * fit.sin;
    p.cx2 += weight * fit.c[0] * fit.c[0]; p.cy2 += weight * fit.c[1] * fit.c[1];
    p.s2 += weight * fit.s * fit.s;
    return p;
  }

  static _pendingMean(p) {
    const c = [p.cx / p.w, p.cy / p.w];
    const cos = p.co / p.w, sin = p.si / p.w;
    return { c, cos, sin, s: Math.hypot(cos, sin) };
  }

  /** Loose sanity gate - reject only estimates that clearly disagree. */
  static _pendingSane(p) {
    const varX = Math.max(0, p.cx2 / p.w - (p.cx / p.w) ** 2);
    const varY = Math.max(0, p.cy2 / p.w - (p.cy / p.w) ** 2);
    const sMean = Math.sqrt(Math.max(0, p.s2 / p.w));
    if (!(sMean > 1e-6)) return false;
    return Math.sqrt(varX + varY) / sMean < 0.45;
  }

  /**
   * Averaging only helps if the samples come from genuinely different
   * viewpoints - twenty frames from a phone sitting still all share the same
   * error. Require the view to have moved before taking another sample.
   */
  static _viewMoved(prev, proj, minPx) {
    if (!prev) return true;
    let d = 0;
    for (let i = 0; i < 4; i++) d += Math.hypot(proj[i][0] - prev[i][0], proj[i][1] - prev[i][1]);
    return d / 4 > minPx;
  }

  /* -------------------------------------------------- keyframe refinement --
   * Chaining tag B off tag A, then C off B, accumulates error. Frames that saw
   * two or more tags together are kept as keyframes, and the whole map is then
   * re-solved by alternating least squares: fix the tags and solve each
   * keyframe pose, fix the poses and re-solve each tag. A handful of sweeps
   * spreads the error out globally instead of letting it pile up along a chain.
   */
  _considerKeyframe(usable, proj) {
    const obs = usable.filter((d) => MarkerMap.minSide(d.corners) >= this.minRegisterSide);
    if (obs.length < 2) return false;
    const key = obs.map((d) => d.id).sort((a, b) => a - b).join(',');
    const same = this.keyframes.filter((k) => k.key === key);
    if (same.length >= this.keyframesPerView) return false;
    for (const k of same) {
      if (!MarkerMap._viewMoved(k.proj, proj, 9)) return false;
    }
    this.keyframes.push({ key, proj, obs: obs.map((d) => ({ id: d.id, corners: d.corners })) });
    if (this.keyframes.length > this.maxKeyframes) {
      // Evict from whichever view is most over-represented.
      const counts = new Map();
      for (const k of this.keyframes) counts.set(k.key, (counts.get(k.key) || 0) + 1);
      let worstKey = null, worstN = 1;
      for (const [k, n] of counts) if (n > worstN) { worstN = n; worstKey = k; }
      const i = this.keyframes.findIndex((k) => k.key === worstKey);
      this.keyframes.splice(i >= 0 ? i : 0, 1);
    }
    return true;
  }

  /**
   * Estimate one tag's square in paper space from one keyframe, using only the
   * OTHER tags in that frame to define the pose.
   *
   * The leave-one-out part is essential. If the pose were fitted using the tag
   * we are about to update, back-projecting through it would just hand back the
   * value we started with - the whole solve sits still at whatever the map
   * happens to already say. Excluding the target makes each observation an
   * independent vote on where it really is.
   */
  _estimateFromFrame(target, others) {
    const src = [], dst = [];
    let ax = 0, ay = 0, an = 0;
    for (const o of others) {
      const e = this.map.get(o.id);
      if (!e) continue;
      const c = MarkerMap.corners(e);
      for (let i = 0; i < 4; i++) {
        src.push(c[i]); dst.push(o.corners[i]);
        ax += o.corners[i][0]; ay += o.corners[i][1]; an++;
      }
    }
    if (an < 4) return null;
    const H = solveHomography(src, dst);
    if (!H) return null;
    const Hinv = matInv(H);
    if (!Hinv) return null;

    ax /= an; ay /= an;
    let spread = 0;
    for (let i = 0; i < dst.length; i++) spread += (dst[i][0] - ax) ** 2 + (dst[i][1] - ay) ** 2;
    spread = Math.sqrt(spread / an);

    const quad = target.corners.map((c) => matApply(Hinv, c[0], c[1]));
    const fit = MarkerMap.fitSquare(quad);
    if (!fit || fit.resid > 0.25) return null;

    let dx = 0, dy = 0;
    for (const c of target.corners) { dx += c[0] / 4; dy += c[1] / 4; }
    const dist = Math.hypot(dx - ax, dy - ay);
    const cond = spread / Math.max(spread, dist);
    fit.w = cond * cond * (an >= 8 ? 1 : 0.35);
    return fit;
  }

  /** Gauss-Seidel sweeps over the tags, each re-solved from every keyframe. */
  refineFromKeyframes(iterations = 4) {
    if (this.keyframes.length < 1 || this.map.size < 2) return;
    for (let it = 0; it < iterations; it++) {
      let moved = 0;
      for (const [id, e] of this.map) {
        if (e.seed) continue;
        let w = 0, cx = 0, cy = 0, co = 0, si = 0;
        for (const kf of this.keyframes) {
          const target = kf.obs.find((o) => o.id === id);
          if (!target) continue;
          const others = kf.obs.filter((o) => o.id !== id && this.map.has(o.id));
          if (!others.length) continue;
          const fit = this._estimateFromFrame(target, others);
          if (!fit) continue;
          w += fit.w;
          cx += fit.w * fit.c[0]; cy += fit.w * fit.c[1];
          co += fit.w * fit.cos; si += fit.w * fit.sin;
        }
        if (w <= 1e-9) continue;
        const nx = cx / w, ny = cy / w, nc = co / w, ns = si / w;
        moved = Math.max(moved, Math.hypot(nx - e.c[0], ny - e.c[1]));
        // Under-relax: keyframes disagree, and stepping all the way there each
        // sweep makes the whole map ring instead of settling.
        const k = 0.65;
        e.c = [e.c[0] + (nx - e.c[0]) * k, e.c[1] + (ny - e.c[1]) * k];
        e.cos += (nc - e.cos) * k;
        e.sin += (ns - e.sin) * k;
        e.s = Math.hypot(e.cos, e.sin);
        e.bad = 0;
      }
      if (moved < 1e-4) break;
    }
  }

  /**
   * @param dets detections from MarkerDetector, in detection-image pixels
   * @returns pose info; H maps paper units -> detection-image pixels
   */
  update(dets) {
    const usable = dets.filter((d) => MarkerMap.minSide(d.corners) >= 10);
    let known = usable.filter((d) => this.map.has(d.id));

    if (!known.length) {
      if (!this.map.size && usable.length) {
        let seed = usable[0], area = 0;
        for (const d of usable) {
          const a = Math.abs(polygonArea(d.corners));
          if (a > area) { area = a; seed = d; }
        }
        // The seed defines the paper frame, so it is never refined afterwards -
        // moving it would drag the whole drawing across the page.
        this.map.set(seed.id, { c: [0, 0], s: 1, cos: 1, sin: 0,
                                wsum: Infinity, bad: 0, seed: true });
        this.seedId = seed.id;
        known = [seed];
      } else {
        this.lost++;
        const holding = !!this.smoothed && this.lost <= this.holdFrames;
        return { H: holding ? this.lastH : null, quality: 0, tracking: false,
                 holding, visible: usable, known: 0, registered: [] };
      }
    }

    const pose = this._solvePose(known);
    if (!pose) {
      this.lost++;
      const holding = this.lost <= this.holdFrames;
      return { H: holding ? this.lastH : null, quality: 0, tracking: false, holding,
               visible: usable, known: known.length, registered: [] };
    }

    // ------------------------------------------------ temporal smoothing ----
    const proj = REF_SQUARE.map((p) => matApply(pose.H, p[0], p[1]));

    // Spike rejection. A pose from a single tag is 4 points fitting 8 degrees
    // of freedom, so once in a while corner noise produces a pose that is
    // perfectly valid geometry and completely wrong. Damping every frame to
    // guard against it just trades noise for lag, so instead detect the
    // impossible frame and hold - but give in after a few in case the phone
    // really did move that fast.
    let jump = 0;
    if (this.lastProj) {
      for (let i = 0; i < 4; i++) {
        jump = Math.max(jump, Math.hypot(proj[i][0] - this.lastProj[i][0],
                                         proj[i][1] - this.lastProj[i][1]));
      }
    }
    if (this.lastProj && this.lost === 0 && known.length === 1 && this.rejects < 3) {
      // Foreshortening ratios are scale-free and change slowly under real
      // motion, so a sudden swing in them means the pose, not the phone.
      const shape = (q) => [
        Math.hypot(q[1][0] - q[0][0], q[1][1] - q[0][1])
          / (Math.hypot(q[2][0] - q[3][0], q[2][1] - q[3][1]) || 1e-6),
        Math.hypot(q[3][0] - q[0][0], q[3][1] - q[0][1])
          / (Math.hypot(q[2][0] - q[1][0], q[2][1] - q[1][1]) || 1e-6),
      ];
      const a = shape(this.lastProj), b = shape(proj);
      const warp = Math.max(Math.abs(Math.log(b[0] / a[0])), Math.abs(Math.log(b[1] / a[1])));
      if (jump > 34 + 3 * this.motion || warp > 0.45) {
        this.rejects++;
        this.lost++;
        return { H: this.lastH, quality: this.quality * 0.5, tracking: false,
                 holding: true, rejected: true, visible: usable,
                 known: known.length, registered: [] };
      }
    }
    this.motion = this.motion * 0.8 + Math.min(jump, 30) * 0.2;
    this.rejects = 0;
    this.lastProj = proj;
    if (!this.smoothed || this.lost > this.holdFrames) {
      this.smoothed = proj;
    } else {
      let motion = 0;
      for (let i = 0; i < 4; i++) {
        motion = Math.max(motion, Math.hypot(proj[i][0] - this.smoothed[i][0],
                                             proj[i][1] - this.smoothed[i][1]));
      }
      // Snappy while the phone moves, heavily damped when nearly still - which
      // is exactly when residual jitter is most visible.
      const alpha = Math.min(1, 0.30 + motion / 26);
      this.smoothed = proj.map((p, i) => [
        this.smoothed[i][0] + (p[0] - this.smoothed[i][0]) * alpha,
        this.smoothed[i][1] + (p[1] - this.smoothed[i][1]) * alpha,
      ]);
    }
    const Hs = solveHomography(REF_SQUARE, this.smoothed) || pose.H;
    this.lastH = Hs;
    this.lost = 0;
    this.quality = 1 / (1 + pose.err);

    // ------------------------------------------- grow and refine the map ----
    const registered = [];
    const Hinv = matInv(pose.H);
    let anchorSide = 0;
    for (const k of known) anchorSide = Math.max(anchorSide, MarkerMap.minSide(k.corners));

    if (Hinv && pose.err <= this.maxReproj && anchorSide >= this.minRegisterSide) {
      for (const d of usable) {
        if (MarkerMap.minSide(d.corners) < this.minRegisterSide) continue;
        const entry = this.map.get(d.id);
        if (entry && (entry.seed || entry.wsum >= this.convergedWeight)) continue;

        // Always exclude the tag itself from the pose used to place it. Fitting
        // through a pose that already contains the tag simply hands back the
        // value we started with, so a bad early estimate could never correct.
        const others = known.filter((k) => k.id !== d.id);
        if (!others.length) continue;
        const fit = this._estimateFromFrame(d, others);
        if (!fit || fit.resid > 0.14 || fit.w < 0.004) continue;

        if (!entry) {
          const p = this.pending.get(d.id);
          if (!MarkerMap._viewMoved(p && p.lastView, proj, 5)) continue;
          const acc = this._accumulate(d.id, fit, fit.w);
          acc.lastView = proj;
          if (acc.n >= this.minSamples && MarkerMap._pendingSane(acc)) {
            const m = MarkerMap._pendingMean(acc);
            this.map.set(d.id, { ...m, wsum: acc.w, bad: 0, seed: false, lastView: proj });
            this.pending.delete(d.id);
            registered.push(d.id);
          }
        } else {
          if (!MarkerMap._viewMoved(entry.lastView, proj, 5)) continue;
          // Weighted running mean: a confident observation can overwrite an
          // early, badly-conditioned guess rather than slowly nudging it.
          const a = fit.w / (entry.wsum + fit.w);
          entry.c = [entry.c[0] + (fit.c[0] - entry.c[0]) * a,
                     entry.c[1] + (fit.c[1] - entry.c[1]) * a];
          entry.cos += (fit.cos - entry.cos) * a;
          entry.sin += (fit.sin - entry.sin) * a;
          entry.s = Math.hypot(entry.cos, entry.sin);
          entry.wsum += fit.w;
          entry.lastView = proj;
        }
      }
    }

    if (pose.err <= this.maxReproj) {
      const added = this._considerKeyframe(usable, proj);
      if (added) this.sinceRefine++;
      if (registered.length || (added && this.sinceRefine >= 3)) {
        this.refineFromKeyframes(registered.length ? 6 : 3);
        this.sinceRefine = 0;
      }
    }

    // A tag whose stored position keeps disagreeing with everyone else is
    // worse than no tag at all - drop it and let it be learnt again.
    for (const k of known) {
      const e = this.map.get(k.id);
      if (e && !e.seed && e.bad > this.maxBad) { this.map.delete(k.id); this.pending.delete(k.id); }
    }

    return { H: Hs, rawH: pose.H, quality: this.quality, tracking: true, holding: false,
             visible: usable, known: known.length, used: pose.pool.length,
             err: pose.err, registered };
  }
}
