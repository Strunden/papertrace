/* ---------------------------------------------------------------------------
 * geom.js - 3x3 projective geometry helpers.
 * Homographies are plain 9-element arrays in row-major order.
 * ------------------------------------------------------------------------- */

function matMul(A, B) {
  const C = new Float64Array(9);
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) {
      C[r * 3 + c] = A[r * 3] * B[c] + A[r * 3 + 1] * B[3 + c] + A[r * 3 + 2] * B[6 + c];
    }
  }
  return C;
}

function matApply(H, x, y) {
  const w = H[6] * x + H[7] * y + H[8];
  return [(H[0] * x + H[1] * y + H[2]) / w, (H[3] * x + H[4] * y + H[5]) / w];
}

/** Returns [X, Y, W] without the perspective divide - needed by the GL renderer. */
function matApplyRaw(H, x, y) {
  return [H[0] * x + H[1] * y + H[2],
          H[3] * x + H[4] * y + H[5],
          H[6] * x + H[7] * y + H[8]];
}

function matInv(H) {
  const a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5],
        g = H[6], h = H[7], i = H[8];
  const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
  const det = a * A + b * B + c * C;
  if (!det || !isFinite(det)) return null;
  const s = 1 / det;
  return new Float64Array([
    A * s, (c * h - b * i) * s, (b * f - c * e) * s,
    B * s, (a * i - c * g) * s, (c * d - a * f) * s,
    C * s, (b * g - a * h) * s, (a * e - b * d) * s,
  ]);
}

/* --------------------------------------------------------------- eigen ----
 * Cyclic Jacobi eigenvalue decomposition for a symmetric n x n matrix.
 * Used to get the null-space vector of A'A when solving a homography, which
 * is far better conditioned than forcing h33 = 1.
 */
function smallestEigenvector(M, n) {
  const a = Float64Array.from(M);
  const v = new Float64Array(n * n);
  for (let i = 0; i < n; i++) v[i * n + i] = 1;

  let off0 = 0;
  for (let p = 0; p < n; p++) {
    for (let q = p + 1; q < n; q++) off0 += a[p * n + q] * a[p * n + q];
  }
  const tol = Math.max(1e-300, off0 * 1e-22);

  for (let sweep = 0; sweep < 30; sweep++) {
    let off = 0;
    for (let p = 0; p < n; p++) {
      for (let q = p + 1; q < n; q++) off += a[p * n + q] * a[p * n + q];
    }
    if (off < tol) break;
    for (let p = 0; p < n; p++) {
      for (let q = p + 1; q < n; q++) {
        const apq = a[p * n + q];
        if (Math.abs(apq) < 1e-30) continue;
        const theta = (a[q * n + q] - a[p * n + p]) / (2 * apq);
        const t = Math.sign(theta || 1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
        const c = 1 / Math.sqrt(t * t + 1), s = t * c;
        for (let k = 0; k < n; k++) {
          const akp = a[k * n + p], akq = a[k * n + q];
          a[k * n + p] = c * akp - s * akq;
          a[k * n + q] = s * akp + c * akq;
        }
        for (let k = 0; k < n; k++) {
          const apk = a[p * n + k], aqk = a[q * n + k];
          a[p * n + k] = c * apk - s * aqk;
          a[q * n + k] = s * apk + c * aqk;
        }
        for (let k = 0; k < n; k++) {
          const vkp = v[k * n + p], vkq = v[k * n + q];
          v[k * n + p] = c * vkp - s * vkq;
          v[k * n + q] = s * vkp + c * vkq;
        }
      }
    }
  }
  let best = 0, bestVal = Infinity;
  for (let i = 0; i < n; i++) {
    if (a[i * n + i] < bestVal) { bestVal = a[i * n + i]; best = i; }
  }
  const out = new Float64Array(n);
  for (let k = 0; k < n; k++) out[k] = v[k * n + best];
  return out;
}

/** Hartley normalisation: translate to centroid, scale so mean distance = sqrt(2). */
function normalizePoints(pts) {
  let cx = 0, cy = 0;
  for (const p of pts) { cx += p[0]; cy += p[1]; }
  cx /= pts.length; cy /= pts.length;
  let d = 0;
  for (const p of pts) d += Math.hypot(p[0] - cx, p[1] - cy);
  d /= pts.length;
  const s = d > 1e-12 ? Math.SQRT2 / d : 1;
  const T = new Float64Array([s, 0, -s * cx, 0, s, -s * cy, 0, 0, 1]);
  const out = pts.map((p) => [(p[0] - cx) * s, (p[1] - cy) * s]);
  return { T, out };
}

/**
 * Closed-form homography taking the unit square (0,0),(1,0),(1,1),(0,1)
 * to the four points of `q`. Exact and far cheaper than the iterative solver.
 */
function unitSquareTo(q) {
  const [x0, y0] = q[0], [x1, y1] = q[1], [x2, y2] = q[2], [x3, y3] = q[3];
  const sx = x0 - x1 + x2 - x3, sy = y0 - y1 + y2 - y3;
  if (Math.abs(sx) < 1e-12 && Math.abs(sy) < 1e-12) {
    return new Float64Array([x1 - x0, x2 - x1, x0, y1 - y0, y2 - y1, y0, 0, 0, 1]);
  }
  const dx1 = x1 - x2, dx2 = x3 - x2, dy1 = y1 - y2, dy2 = y3 - y2;
  const den = dx1 * dy2 - dy1 * dx2;
  if (Math.abs(den) < 1e-12) return null;
  const g = (sx * dy2 - sy * dx2) / den;
  const h = (dx1 * sy - dy1 * sx) / den;
  return new Float64Array([
    x1 - x0 + g * x1, x3 - x0 + h * x3, x0,
    y1 - y0 + g * y1, y3 - y0 + h * y3, y0,
    g, h, 1,
  ]);
}

/**
 * Least-squares homography mapping src -> dst (>= 4 correspondences).
 * Returns a 9-element Float64Array, or null if degenerate.
 */
function solveHomography(src, dst) {
  const n = src.length;
  if (n < 4 || dst.length !== n) return null;

  if (n === 4) {
    const A = unitSquareTo(dst), B = unitSquareTo(src);
    if (A && B) {
      const Bi = matInv(B);
      if (Bi) {
        const H = matMul(A, Bi);
        if (isFinite(H[8]) && Math.abs(H[8]) > 1e-14) {
          for (let i = 0; i < 9; i++) H[i] /= H[8];
          return H;
        }
      }
    }
  }
  const ns = normalizePoints(src), nd = normalizePoints(dst);

  // Accumulate A'A (9x9) directly; avoids materialising the 2n x 9 matrix.
  const ATA = new Float64Array(81);
  const row = new Float64Array(9);
  const accumulate = () => {
    for (let i = 0; i < 9; i++) {
      const ri = row[i];
      if (ri === 0) continue;
      for (let j = 0; j < 9; j++) ATA[i * 9 + j] += ri * row[j];
    }
  };
  for (let k = 0; k < n; k++) {
    const [x, y] = ns.out[k], [u, v] = nd.out[k];
    row.set([-x, -y, -1, 0, 0, 0, u * x, u * y, u]); accumulate();
    row.set([0, 0, 0, -x, -y, -1, v * x, v * y, v]); accumulate();
  }
  const h = smallestEigenvector(ATA, 9);
  const Hn = new Float64Array(h);
  const H = matMul(matInv(nd.T), matMul(Hn, ns.T));
  if (!H || !isFinite(H[8]) || Math.abs(H[8]) < 1e-14) return null;
  for (let i = 0; i < 9; i++) H[i] /= H[8];
  return H;
}

/** Mean reprojection error of src -> dst under H. */
function reprojError(H, src, dst) {
  let e = 0;
  for (let i = 0; i < src.length; i++) {
    const p = matApply(H, src[i][0], src[i][1]);
    e += Math.hypot(p[0] - dst[i][0], p[1] - dst[i][1]);
  }
  return e / src.length;
}

/** Translation * rotation * scale, as a 3x3. */
function trs(tx, ty, angle, sx, sy) {
  const c = Math.cos(angle), s = Math.sin(angle);
  return new Float64Array([c * sx, -s * sy, tx, s * sx, c * sy, ty, 0, 0, 1]);
}

function polygonArea(pts) {
  let a = 0;
  for (let i = 0, n = pts.length; i < n; i++) {
    const p = pts[i], q = pts[(i + 1) % n];
    a += p[0] * q[1] - q[0] * p[1];
  }
  return a / 2;
}
