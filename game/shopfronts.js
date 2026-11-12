// 3D shopfronts, one per shop unit (tools/process.py shop_units): pilasters, fascia with the shop's name,
// cornice, glazing with mullions and transoms, stallriser, door and optional awning. Three styles:
//   trad   - painted timber Victorian/Georgian front: consoles, recessed door, many-paned windows
//   modern - flat fascia, slim aluminium frame, full-height glass, side door
//   pub    - dark painted front, small-paned windows, hanging sign
import * as THREE from 'three';

const hex = (h) => new THREE.Color(h);

function glassTexture() {
  const c = document.createElement('canvas'); c.width = c.height = 256;
  const x = c.getContext('2d');
  x.fillStyle = '#26303a'; x.fillRect(0, 0, 256, 256);
  // shop interior: warm light, shelves and goods
  const g = x.createLinearGradient(0, 0, 0, 256);
  g.addColorStop(0, 'rgba(255,232,190,0.16)'); g.addColorStop(1, 'rgba(255,225,180,0.34)');
  x.fillStyle = g; x.fillRect(0, 0, 256, 256);
  for (let i = 0; i < 40; i++) {
    const v = 90 + Math.random() * 120 | 0, hue = Math.random() * 360 | 0;
    x.fillStyle = `hsla(${hue},35%,${v / 3}%,0.55)`;
    x.fillRect(Math.random() * 256, 120 + Math.random() * 120, 6 + Math.random() * 26, 6 + Math.random() * 40);
  }
  x.fillStyle = 'rgba(200,200,190,0.18)'; for (const y of [150, 200]) x.fillRect(0, y, 256, 3);
  // sky / street reflection sweeping across the glass
  const r = x.createLinearGradient(0, 0, 256, 256);
  r.addColorStop(0, 'rgba(205,222,235,0.6)'); r.addColorStop(0.35, 'rgba(140,160,180,0.12)');
  r.addColorStop(0.55, 'rgba(200,215,230,0.25)'); r.addColorStop(1, 'rgba(60,70,80,0.05)');
  x.fillStyle = r; x.fillRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}

function awningTexture(color, stripe) {
  const c = document.createElement('canvas'); c.width = 128; c.height = 64;
  const x = c.getContext('2d');
  x.fillStyle = color; x.fillRect(0, 0, 128, 64);
  if (stripe) { x.fillStyle = '#f4f2ea'; for (let i = 0; i < 128; i += 32) x.fillRect(i + 16, 0, 16, 64); }
  x.fillStyle = 'rgba(0,0,0,0.12)'; x.fillRect(0, 56, 128, 8);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.wrapS = THREE.RepeatWrapping;
  return t;
}

export function buildShopfronts(scene, units, groundY) {
  const paint = { p: [], c: [] };                 // painted timber / metal, vertex coloured
  const glass = { p: [], uv: [] };
  const awn = new Map();                          // awning colour+stripe -> {p, uv}
  const signs = [];                               // fascia lettering, packed into one atlas below
  const col = new THREE.Color();

  for (const u of units) {
    let [ax, az] = u.a, [bx, bz] = u.b;
    const [nx, nz] = u.n;
    // run the front left-to-right as seen from the street, so lettering and door sides read correctly
    if ((bx - ax) * nz - (bz - az) * nx < 0) [ax, az, bx, bz] = [bx, bz, ax, az];
    const L = Math.hypot(bx - ax, bz - az); if (L < 1.5) continue;
    const tx = (bx - ax) / L, tz = (bz - az) / L;
    const y0 = Math.min(groundY(ax, az), groundY(bx, bz), groundY((ax + bx) / 2, (az + bz) / 2));
    // top of the shopfront sits just under the building's first floor (walls use the same line),
    // so a fascia can't rise into a jettied or rendered storey on sloping streets
    const top = (u.y !== undefined ? u.y : y0) + Math.min(u.h, 4.2) - 0.08;
    const H = Math.max(2.3, top - y0);
    const base = hex(u.c), dark = base.clone().multiplyScalar(0.62), light = base.clone().lerp(new THREE.Color(1, 1, 1), 0.18);
    const P = (s, h, d) => [ax + tx * s + nx * d, y0 + h, az + tz * s + nz * d];
    const quad = (A, B, C, D, list, c) => {
      for (const v of [A, B, C, A, C, D]) list.p.push(...v);
      if (c) for (let i = 0; i < 6; i++) list.c.push(c.r, c.g, c.b);
    };
    // box between along-wall s0..s1, height h0..h1, depth d0..d1 (outward from the wall face)
    const box = (s0, s1, h0, h1, d0, d1, c) => {
      if (s1 <= s0 || h1 <= h0) return;
      quad(P(s0, h0, d1), P(s1, h0, d1), P(s1, h1, d1), P(s0, h1, d1), paint, c);           // front
      quad(P(s0, h1, d0), P(s0, h1, d1), P(s1, h1, d1), P(s1, h1, d0), paint, c);           // top
      quad(P(s0, h0, d0), P(s1, h0, d0), P(s1, h0, d1), P(s0, h0, d1), paint, c);           // underside
      quad(P(s0, h0, d0), P(s0, h0, d1), P(s0, h1, d1), P(s0, h1, d0), paint, c);           // ends
      quad(P(s1, h0, d1), P(s1, h0, d0), P(s1, h1, d0), P(s1, h1, d1), paint, c);
    };
    const pane = (s0, s1, h0, h1, d) => {
      if (s1 <= s0 || h1 <= h0) return;
      for (const [s, h] of [[s0, h0], [s1, h0], [s1, h1], [s0, h0], [s1, h1], [s0, h1]]) {
        glass.p.push(...P(s, h, d)); glass.uv.push(s / 2.6, (h - h0) / (h1 - h0));
      }
    };

    const fz0 = H - (u.st === 'modern' ? 0.7 : 0.62), fz1 = H - 0.05;
    const pil = u.st === 'modern' ? 0.1 : 0.3;
    const doorW = Math.min(1.05, L * 0.3);
    const ds0 = u.d === 'c' ? L / 2 - doorW / 2 : u.d === 'l' ? pil + 0.25 : L - pil - 0.25 - doorW;
    const ds1 = ds0 + doorW;
    const glassD = u.st === 'modern' ? 0.06 : 0.11;

    if (u.st === 'modern') {
      const frame = hex('#5f656b');
      box(0, L, fz0, fz1, 0, 0.24, base);                                   // big flat fascia
      box(0, L, 0, 0.14, 0, 0.1, frame);                                    // base rail
      box(0, pil, 0, fz0, 0, 0.12, frame); box(L - pil, L, 0, fz0, 0, 0.12, frame);
      box(pil, L - pil, fz0 - 0.08, fz0, 0, 0.11, frame);                   // head rail
      for (const s of [ds0, ds1]) box(s - 0.04, s + 0.04, 0.14, fz0, 0, 0.11, frame);
      const span0 = pil, span1 = L - pil, n = Math.max(1, Math.round((span1 - span0) / 2.2));
      for (let i = 1; i < n; i++) { const s = span0 + (span1 - span0) * i / n; if (s < ds0 - 0.3 || s > ds1 + 0.3) box(s - 0.035, s + 0.035, 0.14, fz0 - 0.08, 0, 0.1, frame); }
      pane(pil, L - pil, 0.14, fz0 - 0.08, glassD);
      box(ds0 + 0.1, ds1 - 0.1, 1.0, 1.05, glassD, glassD + 0.06, hex('#c9ccd0'));    // door push bar
    } else {
      const pub = u.st === 'pub';
      const stall = pub ? 0.75 : 0.58;
      // pilasters with plinths and console brackets carrying the fascia
      for (const [s0, s1] of [[0, pil], [L - pil, L]]) {
        box(s0, s1, 0, fz0, 0, 0.16, base);
        box(s0 - 0.02, s1 + 0.02, 0, 0.32, 0, 0.19, dark);
        box(s0 - 0.03, s1 + 0.03, fz0 - 0.28, fz1, 0, 0.3, light);
      }
      box(pil, L - pil, fz0, fz1, 0, 0.18, base);                           // fascia board
      box(-0.05, L + 0.05, fz1, fz1 + 0.11, 0, 0.27, light);                // cornice
      box(-0.05, L + 0.05, fz1 + 0.11, fz1 + 0.14, 0, 0.32, light);
      // stallriser with a sunk panel, and the sill on top
      for (const [s0, s1] of [[pil, ds0], [ds1, L - pil]]) {
        if (s1 - s0 < 0.2) continue;
        box(s0, s1, 0, stall, 0, 0.12, base);
        box(s0 + 0.12, s1 - 0.12, 0.12, stall - 0.12, 0.12, 0.14, dark);
        box(s0, s1, stall, stall + 0.06, 0, glassD + 0.06, light);
      }
      // transom bar, transom lights above it, display glazing below, divided by mullions
      const tr = fz0 - 0.5;
      box(pil, L - pil, tr - 0.06, tr, 0, glassD + 0.05, base);
      pane(pil, L - pil, tr, fz0, glassD);
      for (const [s0, s1] of [[pil, ds0 - 0.08], [ds1 + 0.08, L - pil]]) {
        if (s1 - s0 < 0.25) continue;
        pane(s0, s1, stall + 0.06, tr - 0.06, glassD);
        const step = pub ? 0.42 : 0.85, n = Math.max(1, Math.round((s1 - s0) / step));
        for (let i = 1; i < n; i++) { const s = s0 + (s1 - s0) * i / n; box(s - 0.03, s + 0.03, stall + 0.06, tr - 0.06, glassD - 0.01, glassD + 0.04, base); }
        if (pub) for (const f of [0.33, 0.66]) { const h = stall + 0.06 + (tr - stall - 0.12) * f; box(s0, s1, h - 0.025, h + 0.025, glassD - 0.01, glassD + 0.04, base); }
      }
      // recessed door with reveals and a glazed upper half
      for (const [s0, s1] of [[ds0 - 0.08, ds0], [ds1, ds1 + 0.08]]) box(s0, s1, 0, tr - 0.06, 0.02, glassD + 0.05, base);
      box(ds0, ds1, 0, tr - 0.06, 0.0, 0.04, dark);
      pane(ds0 + 0.12, ds1 - 0.12, (tr - 0.06) * 0.5, tr - 0.18, 0.045);
      box(ds0 + doorW * 0.75, ds0 + doorW * 0.8, 1.0, 1.1, 0.04, 0.08, hex('#c8a24a'));   // brass handle
      if (pub) {   // hanging sign on a bracket above the fascia
        const s = L * 0.5;
        box(s - 0.04, s + 0.04, H + 0.55, H + 0.6, 0, 1.0, hex('#1a1a1a'));
        box(s - 0.03, s + 0.03, H - 0.25, H + 0.55, 0.62, 0.68, dark);
        box(s - 0.33, s + 0.33, H - 0.2, H + 0.5, 0.6, 0.7, base);
      }
    }

    // awning: sloping canvas over the window, from just under the fascia out over the pavement
    if (u.aw) {
      const ac = u.ac || u.c, key = ac + u.aw;
      if (!awn.has(key)) awn.set(key, { p: [], uv: [], tex: awningTexture(ac, u.aw === 'stripe') });
      const A = awn.get(key), top = fz0 - 0.02, out = 1.35, drop = 0.7, s0 = pil * 0.5, s1 = L - pil * 0.5;
      const push = (pts, uvs) => { for (const i of [0, 1, 2, 0, 2, 3]) { A.p.push(...pts[i]); A.uv.push(...uvs[i]); } };
      push([P(s0, top, 0.25), P(s1, top, 0.25), P(s1, top - drop, out), P(s0, top - drop, out)], [[0, 1], [(s1 - s0) / 1.3, 1], [(s1 - s0) / 1.3, 0.15], [0, 0.15]]);
      push([P(s0, top - drop, out), P(s1, top - drop, out), P(s1, top - drop - 0.22, out), P(s0, top - drop - 0.22, out)], [[0, 0.15], [(s1 - s0) / 1.3, 0.15], [(s1 - s0) / 1.3, 0], [0, 0]]);
    }

    // fascia lettering: the sign is the fascia board itself, edge to edge between the pilasters
    if (u.name) {
      const s0 = u.st === 'modern' ? 0.05 : pil + 0.02, s1 = L - s0;
      const d = (u.st === 'modern' ? 0.24 : 0.18) + 0.004;
      signs.push({ u, A: P(s0, fz0 + 0.03, d), B: P(s1, fz0 + 0.03, d), C: P(s1, fz1 - 0.03, d), D: P(s0, fz1 - 0.03, d), w: s1 - s0, h: fz1 - fz0 - 0.06 });
    }
  }

  const add = (g, m) => { const mesh = new THREE.Mesh(g, m); mesh.castShadow = mesh.receiveShadow = true; scene.add(mesh); return mesh; };
  {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(paint.p, 3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(paint.c, 3));
    g.computeVertexNormals();
    add(g, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.55, metalness: 0.05, side: THREE.DoubleSide }));
  }
  {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(glass.p, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(glass.uv, 2));
    g.computeVertexNormals();
    const m = add(g, new THREE.MeshStandardMaterial({ map: glassTexture(), roughness: 0.12, metalness: 0.35, emissive: 0x2a2014, emissiveIntensity: 0.6, side: THREE.DoubleSide }));
    m.castShadow = false;
  }
  for (const A of awn.values()) {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(A.p, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(A.uv, 2));
    g.computeVertexNormals();
    add(g, new THREE.MeshStandardMaterial({ map: A.tex, roughness: 0.9, side: THREE.DoubleSide }));
  }

  // sign atlas: shelf-pack one strip per sign; lettering in serif for traditional fronts, sans for modern
  if (signs.length) {
    const W = 4096, CH = 64, cells = [];
    let cx = 0, cy = 0;
    for (const s of signs) {
      const cw = Math.min(W, Math.max(128, Math.round(CH * s.w / s.h)));
      if (cx + cw > W) { cx = 0; cy += CH; }
      cells.push([cx, cy, cw]); cx += cw;
    }
    const Hh = Math.min(4096, 1 << Math.ceil(Math.log2(cy + CH)));
    const c = document.createElement('canvas'); c.width = W; c.height = Hh;
    const x = c.getContext('2d');
    const pos = [], uv = [];
    signs.forEach((s, i) => {
      const [px, py, cw] = cells[i];
      x.fillStyle = s.u.sc; x.fillRect(px, py, cw, CH);
      const bg = new THREE.Color(s.u.sc), lum = bg.r * 0.3 + bg.g * 0.59 + bg.b * 0.11;
      const ink = s.u.st === 'modern' ? (lum > 0.55 ? '#1d1d1f' : '#ffffff') : (lum > 0.55 ? '#2a2620' : '#e9d8a6');
      if (s.u.st !== 'modern') { x.strokeStyle = ink; x.globalAlpha = 0.5; x.lineWidth = 2; x.strokeRect(px + 6, py + 6, cw - 12, CH - 12); x.globalAlpha = 1; }
      x.fillStyle = ink;
      x.font = s.u.st === 'modern' ? '700 40px "Helvetica Neue", Arial, sans-serif' : '600 38px Georgia, "Times New Roman", serif';
      const label = s.u.st === 'modern' ? s.u.name : s.u.name;
      const tw = x.measureText(label).width, maxw = cw - 40;
      x.save(); x.translate(px + cw / 2, py + CH / 2 + 13);
      if (tw > maxw) x.scale(maxw / tw, 1);
      x.textAlign = 'center'; x.fillText(label, 0, 0); x.restore();
      const u0 = px / W, u1 = (px + cw) / W, v1 = 1 - py / Hh, v0 = 1 - (py + CH) / Hh;
      for (const [p, t] of [[s.A, [u0, v0]], [s.B, [u1, v0]], [s.C, [u1, v1]], [s.A, [u0, v0]], [s.C, [u1, v1]], [s.D, [u0, v1]]]) { pos.push(...p); uv.push(...t); }
    });
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    g.computeVertexNormals();
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8;
    const m = add(g, new THREE.MeshStandardMaterial({ map: t, roughness: 0.5, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 }));
    m.castShadow = false;
  }
}
