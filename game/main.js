import * as THREE from 'three';
import { makeFacadeAtlases, facadeMaterial, roofTexture } from './facades.js';
import { buildStreet, buildPavements } from './street.js';
import { buildShopfronts } from './shopfronts.js';

// ------------------------------------------------------------------ loading
const D = 'data/';
const bar = document.querySelector('#bar i');
let loaded = 0;
const FILES = 37;
const tick = () => { loaded++; bar.style.width = `${Math.min(100, loaded / FILES * 100)}%`; };
// every data file carries the map build stamp, so an updated map never mixes with cached old files
const BUILD = (await (await fetch(D + 'meta.json', { cache: 'no-store' })).json()).build || '';
const V = (f) => D + f + (BUILD ? `?v=${BUILD}` : '');
const bin = async (f, T) => { const b = await (await fetch(V(f))).arrayBuffer(); tick(); return new T(b); };
const json = async (f) => { const j = await (await fetch(V(f))).json(); tick(); return j; };

const [terrMeta, terrH, driveH, kindG, roads, foots, water, trees, meta, shopUnits, street] = await Promise.all([
  json('terrain.json'), bin('terrain.bin', Uint16Array), bin('drive.bin', Uint16Array), bin('kind.bin', Uint8Array),
  json('roads.json'), json('footprints.json'), json('water.json'), json('trees.json'), json('meta.json'), json('shopfronts.json'), json('street.json'),
]);
const wallData = await Promise.all([0, 1, 2, 3, 4, 5].map(async s => ({
  pos: await bin(`walls${s}_pos.bin`, Float32Array), uv: await bin(`walls${s}_uv.bin`, Float32Array),
  col: await bin(`walls${s}_col.bin`, Uint8Array), kind: await bin(`walls${s}_kind.bin`, Uint8Array),
})));
const roof = { pos: await bin('roof_pos.bin', Float32Array), uv: await bin('roof_uv.bin', Float32Array), col: await bin('roof_col.bin', Uint8Array) };
const paveTris = await bin('pave_tris.bin', Float32Array), kerbSegs = await bin('kerbs.bin', Float32Array);

// ------------------------------------------------------------------ grids
const GN = terrMeta.n, GS = terrMeta.s, GO = terrMeta.origin, HMIN = terrMeta.min;
const HALF = -GO;
function bilinear(arr, x, z) {
  let fx = (x - GO) / GS - 0.5, fz = (z - GO) / GS - 0.5;
  fx = Math.max(0, Math.min(GN - 1.001, fx)); fz = Math.max(0, Math.min(GN - 1.001, fz));
  const i = Math.floor(fx), j = Math.floor(fz), tx = fx - i, tz = fz - j;
  const a = arr[j * GN + i], b = arr[j * GN + i + 1], c = arr[(j + 1) * GN + i], d = arr[(j + 1) * GN + i + 1];
  return HMIN + ((a * (1 - tx) + b * tx) * (1 - tz) + (c * (1 - tx) + d * tx) * tz) / 100;
}
const terrainY = (x, z) => bilinear(terrH, x, z);
const driveY = (x, z) => bilinear(driveH, x, z);
function kindAt(x, z) {
  const i = Math.floor((x - GO) / GS), j = Math.floor((z - GO) / GS);
  if (i < 0 || j < 0 || i >= GN || j >= GN) return 0;
  return kindG[j * GN + i];
}

// ------------------------------------------------------------------ renderer / scene
const canvas = document.getElementById('gl');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, logarithmicDepthBuffer: false });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
renderer.outputColorSpace = THREE.SRGBColorSpace;

const scene = new THREE.Scene();
const SKY = new THREE.Color(0xb4c6d4);
scene.background = SKY;
scene.fog = new THREE.Fog(SKY, 250, 1300);

const camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.3, 3000);
addEventListener('resize', () => {
  renderer.setSize(innerWidth, innerHeight);
  camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
});

scene.add(new THREE.HemisphereLight(0xdfe8f0, 0x5a6040, 1.15));
const sun = new THREE.DirectionalLight(0xfff1dc, 2.1);
sun.castShadow = true;
sun.shadow.mapSize.set(4096, 4096);
const SC = sun.shadow.camera; SC.left = -140; SC.right = 140; SC.top = 140; SC.bottom = -140; SC.near = 10; SC.far = 700;
sun.shadow.bias = -0.0004; sun.shadow.normalBias = 0.6;
const SUN_DIR = new THREE.Vector3(-0.45, 0.75, 0.48).normalize();
scene.add(sun, sun.target);

// sky dome with gradient
{
  const g = new THREE.SphereGeometry(2500, 32, 16);
  const m = new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false,
    uniforms: { top: { value: new THREE.Color(0x6f9cc8) }, bot: { value: SKY } },
    vertexShader: 'varying vec3 vp; void main(){ vp = position; gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.); }',
    fragmentShader: 'uniform vec3 top; uniform vec3 bot; varying vec3 vp; void main(){ float h = clamp(normalize(vp).y*2.2,0.,1.); gl_FragColor = vec4(mix(bot,top,h),1.); }',
  });
  const sky = new THREE.Mesh(g, m); sky.renderOrder = -10; sky.frustumCulled = false;
  scene.add(sky);
  window._sky = sky;
}

// ------------------------------------------------------------------ textures
function canvasTex(w, h, draw, repeat = true) {
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  draw(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  if (repeat) t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.anisotropy = renderer.capabilities.getMaxAnisotropy();
  return t;
}
function noise(ctx, w, h, amt, alpha = 0.08) {
  for (let i = 0; i < amt; i++) {
    const v = Math.random() * 255 | 0;
    ctx.fillStyle = `rgba(${v},${v},${v},${alpha})`;
    ctx.fillRect(Math.random() * w, Math.random() * h, 2, 2);
  }
}
const roadTex = (marked) => canvasTex(64, 256, (x, w, h) => {
  x.fillStyle = '#5a5c5e'; x.fillRect(0, 0, w, h); noise(x, w, h, 3000, 0.12);
  if (marked) {
    x.fillStyle = '#e8e6de'; x.fillRect(w / 2 - 2, 0, 4, h * 0.5);
    x.fillStyle = 'rgba(232,230,222,.8)'; x.fillRect(2, 0, 2, h); x.fillRect(w - 4, 0, 2, h);
  }
});

// ------------------------------------------------------------------ terrain
{
  const pos = new Float32Array(GN * GN * 3), uv = new Float32Array(GN * GN * 2);
  for (let j = 0; j < GN; j++) for (let i = 0; i < GN; i++) {
    const k = j * GN + i, x = GO + (i + 0.5) * GS, z = GO + (j + 0.5) * GS;
    pos[k * 3] = x; pos[k * 3 + 1] = HMIN + terrH[k] / 100; pos[k * 3 + 2] = z;
    uv[k * 2] = (x - GO) / (2 * HALF); uv[k * 2 + 1] = 1 - (z - GO) / (2 * HALF);
  }
  const idx = new Uint32Array((GN - 1) * (GN - 1) * 6);
  let n = 0;
  for (let j = 0; j < GN - 1; j++) for (let i = 0; i < GN - 1; i++) {
    const a = j * GN + i, b = a + 1, c = a + GN, d = c + 1;
    idx[n++] = a; idx[n++] = c; idx[n++] = b; idx[n++] = b; idx[n++] = c; idx[n++] = d;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  g.setIndex(new THREE.BufferAttribute(idx, 1));
  g.computeVertexNormals();
  const tex = new THREE.TextureLoader().load(V('ground.jpg'), tick);
  tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
  const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ map: tex, roughness: 0.95 }));
  m.receiveShadow = true;
  scene.add(m);
  // surrounding countryside
  const outer = new THREE.Mesh(new THREE.RingGeometry(HALF * 1.0, 3000, 4, 1, Math.PI / 4), new THREE.MeshStandardMaterial({ color: 0x6f8a4c, roughness: 1 }));
  outer.rotation.x = -Math.PI / 2; outer.scale.set(1.4142, 1.4142, 1); outer.position.y = HMIN + 3;
  scene.add(outer);
}

// ------------------------------------------------------------------ water
{
  const mat = new THREE.MeshStandardMaterial({ color: 0x4d6d78, roughness: 0.15, metalness: 0.35, transparent: true, opacity: 0.92 });
  for (const w of water) {
    const s = new THREE.Shape(w.p.map(([x, z]) => new THREE.Vector2(x, -z)));
    for (const h of w.h) s.holes.push(new THREE.Path(h.map(([x, z]) => new THREE.Vector2(x, -z))));
    const g = new THREE.ShapeGeometry(s);
    const m = new THREE.Mesh(g, mat);
    m.rotation.x = -Math.PI / 2; m.position.y = w.y + 0.35; m.receiveShadow = true;
    scene.add(m);
  }
}

// ------------------------------------------------------------------ roads
const ROAD_RANK = { primary: 6, trunk: 7, secondary: 5, tertiary: 4, primary_link: 5, residential: 3, unclassified: 3, living_street: 2, pedestrian: 2, service: 1, track: 0 };
{
  const groups = { marked: { p: [], uv: [], i: [] }, plain: { p: [], uv: [], i: [] } };
  const parapets = [];
  const sorted = [...roads].sort((a, b) => (ROAD_RANK[a.t] ?? 2) - (ROAD_RANK[b.t] ?? 2));
  for (const r of sorted) {
    const marked = r.w >= 5 && !['service', 'track', 'pedestrian', 'living_street'].includes(r.t);
    const G = marked ? groups.marked : groups.plain;
    const pts = r.pts, n = pts.length, hw = r.w / 2;
    if (n < 2) continue;
    const lift = 0.16 + (ROAD_RANK[r.t] ?? 2) * 0.004;
    let dist = 0;
    const base = G.p.length / 3;
    const L = [], R = [];
    for (let k = 0; k < n; k++) {
      const a = pts[Math.max(0, k - 1)], b = pts[Math.min(n - 1, k + 1)];
      let tx = b[0] - a[0], tz = b[1] - a[1]; const tl = Math.hypot(tx, tz) || 1; tx /= tl; tz /= tl;
      const nx = -tz, nz = tx;
      if (k > 0) dist += Math.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]);
      const lx = pts[k][0] + nx * hw, lz = pts[k][1] + nz * hw, rx = pts[k][0] - nx * hw, rz = pts[k][1] - nz * hw;
      let ly, ry;
      if (r.b) { ly = ry = pts[k][2] + lift; }
      else {
        const c = terrainY(pts[k][0], pts[k][1]);
        ly = Math.max(terrainY(lx, lz), c) + lift; ry = Math.max(terrainY(rx, rz), c) + lift;
      }
      G.p.push(lx, ly, lz, rx, ry, rz);
      G.uv.push(0, dist / 6, 1, dist / 6);
      L.push([lx, ly, lz]); R.push([rx, ry, rz]);
      if (k > 0) { const o = base + (k - 1) * 2; G.i.push(o, o + 1, o + 2, o + 1, o + 3, o + 2); }
    }
    // round caps at both ends to close joints
    for (const k of [0, n - 1]) {
      const [cx, cz] = pts[k];
      const cy = r.b ? pts[k][2] + lift : terrainY(cx, cz) + lift - 0.003;
      const o = G.p.length / 3;
      G.p.push(cx, cy, cz); G.uv.push(0.02, 0);
      const seg = 10;
      for (let s = 0; s <= seg; s++) {
        const ang = s / seg * Math.PI * 2, x = cx + Math.cos(ang) * hw, z = cz + Math.sin(ang) * hw;
        G.p.push(x, r.b ? cy : Math.max(terrainY(x, z) + lift - 0.003, cy), z); G.uv.push(0.02, 0);
        if (s > 0) G.i.push(o, o + s + 1, o + s);
      }
    }
    if (r.b) parapets.push(L, R);
  }
  const mk = (G, tex) => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(G.p, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(G.uv, 2));
    g.setIndex(G.i);
    g.computeVertexNormals();
    const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({
      map: tex, roughness: 0.9, side: THREE.DoubleSide, depthWrite: false,
      polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4,
    }));
    m.receiveShadow = true; m.renderOrder = 1;
    scene.add(m);
  };
  mk(groups.marked, roadTex(true));
  mk(groups.plain, roadTex(false));
  // bridge parapets + deck sides
  const pp = [];
  for (const line of parapets) for (let k = 1; k < line.length; k++) {
    const [ax, ay, az] = line[k - 1], [bx, by, bz] = line[k];
    pp.push(ax, ay - 1.6, az, bx, by - 1.6, bz, bx, by + 0.9, bz, ax, ay - 1.6, az, bx, by + 0.9, bz, ax, ay + 0.9, az);
  }
  if (pp.length) {
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(pp, 3)); g.computeVertexNormals();
    const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: 0xb8a888, roughness: 0.9, side: THREE.DoubleSide }));
    m.castShadow = m.receiveShadow = true; scene.add(m);
  }
}

// ------------------------------------------------------------------ buildings
{
  const atlases = makeFacadeAtlases(renderer);
  wallData.forEach((w, s) => {
    if (!w.pos.length) return;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(w.pos, 3));
    g.setAttribute('uv', new THREE.BufferAttribute(w.uv, 2));
    g.setAttribute('color', new THREE.BufferAttribute(w.col, 3, true));
    g.setAttribute('kind', new THREE.BufferAttribute(w.kind, 1));
    g.computeVertexNormals();
    const m = new THREE.Mesh(g, facadeMaterial(atlases[s]));
    m.castShadow = m.receiveShadow = true;
    scene.add(m);
  });
  // roofs: planar LIDAR facets, non-indexed so every facet shades flat; flip any downward-facing triangle
  const P = roof.pos, UV = roof.uv, C = roof.col;
  for (let t = 0; t < P.length; t += 9) {
    const ux = P[t + 3] - P[t], uz = P[t + 5] - P[t + 2], vx = P[t + 6] - P[t], vz = P[t + 8] - P[t + 2];
    if (uz * vx - ux * vz < 0) {
      for (let k = 0; k < 3; k++) { const a = P[t + 3 + k]; P[t + 3 + k] = P[t + 6 + k]; P[t + 6 + k] = a; }
      const tu = t / 9 * 6; for (let k = 0; k < 2; k++) { const a = UV[tu + 2 + k]; UV[tu + 2 + k] = UV[tu + 4 + k]; UV[tu + 4 + k] = a; }
      const tc = t / 9 * 9; for (let k = 0; k < 3; k++) { const a = C[tc + 3 + k]; C[tc + 3 + k] = C[tc + 6 + k]; C[tc + 6 + k] = a; }
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(P, 3));
  g.setAttribute('uv', new THREE.BufferAttribute(UV, 2));
  g.setAttribute('color', new THREE.BufferAttribute(C, 3, true));
  g.computeVertexNormals();
  const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ map: roofTexture(renderer), vertexColors: true, roughness: 0.82, side: THREE.DoubleSide }));
  m.castShadow = m.receiveShadow = true;
  scene.add(m);
}

// ------------------------------------------------------------------ trees (positions & heights from LIDAR canopy)
{
  const crownG = new THREE.IcosahedronGeometry(1, 1);
  const trunkG = new THREE.CylinderGeometry(0.18, 0.26, 1, 6); trunkG.translate(0, 0.5, 0);
  const crowns = new THREE.InstancedMesh(crownG, new THREE.MeshStandardMaterial({ roughness: 0.95, flatShading: true }), trees.length);
  const trunks = new THREE.InstancedMesh(trunkG, new THREE.MeshStandardMaterial({ color: 0x4a3a2c, roughness: 1 }), trees.length);
  const M = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3(), col = new THREE.Color();
  trees.forEach(([x, y, z, h], i) => {
    const r = Math.max(1.6, Math.min(6.5, h * 0.32));
    const rnd = Math.abs(Math.sin(x * 12.9898 + z * 78.233)) % 1;
    q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), rnd * 6.28);
    p.set(x, y + h - r * 0.95, z); s.set(r, r * (0.85 + rnd * 0.3), r);
    M.compose(p, q, s); crowns.setMatrixAt(i, M);
    col.setHSL(0.22 + rnd * 0.08, 0.38 + rnd * 0.15, 0.22 + rnd * 0.08); crowns.setColorAt(i, col);
    p.set(x, y - 0.2, z); s.set(1 + h * 0.02, h - r, 1 + h * 0.02);
    M.compose(p, q.identity(), s); trunks.setMatrixAt(i, M);
  });
  crowns.castShadow = true; crowns.receiveShadow = true; trunks.castShadow = true;
  scene.add(crowns, trunks);
}

// ------------------------------------------------------------------ collision hash (building edges + tree trunks)
const CELL = 16;
const segHash = new Map();
const hkey = (i, j) => i * 100003 + j;
function hashSeg(ax, az, bx, bz) {
  const i0 = Math.floor(Math.min(ax, bx) / CELL), i1 = Math.floor(Math.max(ax, bx) / CELL);
  const j0 = Math.floor(Math.min(az, bz) / CELL), j1 = Math.floor(Math.max(az, bz) / CELL);
  for (let i = i0; i <= i1; i++) for (let j = j0; j <= j1; j++) {
    const k = hkey(i, j); if (!segHash.has(k)) segHash.set(k, []); segHash.get(k).push([ax, az, bx, bz]);
  }
}
for (const f of foots) for (let k = 1; k < f.p.length; k++) hashSeg(f.p[k - 1][0], f.p[k - 1][1], f.p[k][0], f.p[k][1]);
const treeHash = new Map();
for (const [x, , z, h] of trees) {
  const k = hkey(Math.floor(x / CELL), Math.floor(z / CELL)); if (!treeHash.has(k)) treeHash.set(k, []); treeHash.get(k).push([x, z, 0.35 + h * 0.012]);
}
// street furniture + garden walls/fences/hedges (OSM); poles and walls block the car
let onPavement = () => false;
{
  onPavement = buildPavements(scene, paveTris, kerbSegs, terrainY);
  const groundY = (x, z) => terrainY(x, z) + (onPavement(x, z) ? 0.31 : 0);
  const { posts, segs } = buildStreet(scene, street, groundY);
  buildShopfronts(scene, shopUnits, groundY);
  for (const [x, z, r] of posts) {
    const k = hkey(Math.floor(x / CELL), Math.floor(z / CELL)); if (!treeHash.has(k)) treeHash.set(k, []); treeHash.get(k).push([x, z, r]);
  }
  for (const [ax, az, bx, bz] of segs) hashSeg(ax, az, bx, bz);
}
// road segments hash for street-name lookup + respawn
const roadHash = new Map();
roads.forEach((r, ri) => {
  for (let k = 1; k < r.pts.length; k++) {
    const [ax, az] = r.pts[k - 1], [bx, bz] = r.pts[k];
    const i = Math.floor((ax + bx) / 2 / CELL), j = Math.floor((az + bz) / 2 / CELL);
    const key = hkey(i, j); if (!roadHash.has(key)) roadHash.set(key, []); roadHash.get(key).push([ax, az, bx, bz, ri]);
  }
});
function closestOnSeg(px, pz, ax, az, bx, bz) {
  const dx = bx - ax, dz = bz - az, l2 = dx * dx + dz * dz || 1e-9;
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (pz - az) * dz) / l2));
  return [ax + dx * t, az + dz * t];
}
function nearestRoad(x, z, maxD = 60, filter) {
  let best = null, bd = maxD * maxD;
  const R = Math.ceil(maxD / CELL) + 1, ci = Math.floor(x / CELL), cj = Math.floor(z / CELL);
  for (let i = ci - R; i <= ci + R; i++) for (let j = cj - R; j <= cj + R; j++) {
    const L = roadHash.get(hkey(i, j)); if (!L) continue;
    for (const [ax, az, bx, bz, ri] of L) {
      if (filter && !filter(roads[ri])) continue;
      const [qx, qz] = closestOnSeg(x, z, ax, az, bx, bz);
      const d = (qx - x) ** 2 + (qz - z) ** 2;
      if (d < bd) { bd = d; best = { x: qx, z: qz, d: Math.sqrt(d), road: roads[ri], heading: Math.atan2(bx - ax, bz - az) }; }
    }
  }
  return best;
}

// landmarks: named / listed footprints
const landmarks = foots.filter(f => f.n).map(f => {
  let cx = 0, cz = 0; for (const [x, z] of f.p) { cx += x; cz += z; }
  return { n: f.n, g: f.g, m: f.m, x: cx / f.p.length, z: cz / f.p.length };
});

// ------------------------------------------------------------------ car (generic low-poly coupe)
function buildCar(color) {
  const car = new THREE.Group();
  const body = new THREE.Group(); car.add(body);
  const paint = new THREE.MeshStandardMaterial({ color, roughness: 0.35, metalness: 0.4 });
  const glass = new THREE.MeshStandardMaterial({ color: 0x1b242c, roughness: 0.1, metalness: 0.6 });
  const black = new THREE.MeshStandardMaterial({ color: 0x151515, roughness: 0.8 });
  const extr = (pts, width, mat) => {
    const s = new THREE.Shape(pts.map(([a, b]) => new THREE.Vector2(a, b)));
    const g = new THREE.ExtrudeGeometry(s, { depth: width, bevelEnabled: true, bevelThickness: 0.04, bevelSize: 0.04, bevelSegments: 2 });
    g.translate(0, 0, -width / 2); g.rotateY(-Math.PI / 2);
    const m = new THREE.Mesh(g, mat); m.castShadow = true; m.receiveShadow = true; return m;
  };
  body.add(extr([[2.1, 0.32], [2.15, 0.6], [0.45, 0.86], [-2.15, 0.95], [-2.15, 0.32]], 1.66, paint));
  body.add(extr([[0.5, 0.86], [-0.3, 1.24], [-1.4, 1.24], [-1.98, 0.95]], 1.46, glass));
  body.add(extr([[-0.32, 1.22], [-0.3, 1.27], [-1.42, 1.27], [-1.4, 1.22]], 1.36, paint));
  body.add(extr([[2.22, 0.3], [2.22, 0.46], [-2.2, 0.46], [-2.2, 0.3]], 1.7, black));
  const lamp = (x, z, c, e) => { const m = new THREE.Mesh(new THREE.BoxGeometry(0.42, 0.1, 0.05), new THREE.MeshStandardMaterial({ color: c, emissive: e, emissiveIntensity: 1.2 })); m.position.set(x, 0.56, z); body.add(m); };
  lamp(-0.55, 2.16, 0xffffee, 0x666655); lamp(0.55, 2.16, 0xffffee, 0x666655);
  lamp(-0.55, -2.17, 0xaa1111, 0x440000); lamp(0.55, -2.17, 0xaa1111, 0x440000);
  const wheels = [];
  const wg = new THREE.CylinderGeometry(0.33, 0.33, 0.24, 18); wg.rotateZ(Math.PI / 2);
  const hub = new THREE.CylinderGeometry(0.17, 0.17, 0.25, 10); hub.rotateZ(Math.PI / 2);
  const hubM = new THREE.MeshStandardMaterial({ color: 0xbfc3c6, metalness: 0.8, roughness: 0.3 });
  for (const [x, z] of [[-0.78, 1.32], [0.78, 1.32], [-0.78, -1.32], [0.78, -1.32]]) {
    const w = new THREE.Group(); w.position.set(x, 0.33, z);
    const t = new THREE.Mesh(wg, black); t.castShadow = true; w.add(t, new THREE.Mesh(hub, hubM));
    car.add(w); wheels.push(w);
  }
  return { car, body, wheels };
}
const { car, body: carBody, wheels } = buildCar(0xb3261e);
scene.add(car);

// ------------------------------------------------------------------ state
const S = { x: meta.spawn[0], z: meta.spawn[1], y: 0, h: 0, v: 0, vl: 0, steer: 0, pitch: 0, roll: 0, yv: 0, sinking: 0 };
{
  const r = nearestRoad(S.x, S.z, 80, r => r.n === 'High Street') || nearestRoad(S.x, S.z, 80);
  if (r) { S.x = r.x; S.z = r.z; S.h = r.heading; }
  S.y = driveY(S.x, S.z);
}
const _initRoad = nearestRoad(S.x, S.z, 80, r => r.n === 'High Street');
let lastSafe = { x: S.x, z: S.z, h: S.h }, safeTimer = 0;

const keys = {};
let camMode = 0, sound = false, bigMap = false;
addEventListener('keydown', e => {
  keys[e.code] = true;
  if (e.code === 'KeyC') camMode = (camMode + 1) % 4;
  if (e.code === 'KeyM') { bigMap = !bigMap; document.getElementById('mapwrap').classList.toggle('big', bigMap); }
  if (e.code === 'KeyR') respawn();
  if (e.code === 'KeyH') { const h = document.getElementById('help'); h.style.display = h.style.display === 'none' ? '' : 'none'; }
  if (e.code === 'KeyV') toggleSound();
  if (/^Digit[1-7]$/.test(e.code)) teleport(+e.code.slice(5) - 1);
  if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Space'].includes(e.code)) e.preventDefault();
});
addEventListener('keyup', e => { keys[e.code] = false; });

// pick the way along the road (h or h+pi) that stays on the same road for longest
function bestHeading(r) {
  let best = r.heading, bestScore = -1;
  for (const h of [r.heading, r.heading + Math.PI]) {
    let score = 0;
    for (let d = 6; d <= 36; d += 6) {
      const q = nearestRoad(r.x + Math.sin(h) * d, r.z + Math.cos(h) * d, 12, x => x === r.road);
      if (q && q.d < r.road.w / 2) score++; else break;
    }
    if (score > bestScore) { bestScore = score; best = h; }
  }
  return best;
}
function respawn(to) {
  const r = to || nearestRoad(S.x, S.z, 400, r => r.t !== 'track' && !r.b);
  if (r) { S.x = r.x; S.z = r.z; S.h = r.road ? bestHeading(r) : r.heading; }
  else { S.x = lastSafe.x; S.z = lastSafe.z; S.h = lastSafe.h; }
  S.v = 0; S.vl = 0; S.sinking = 0; S.yv = 0; S.y = driveY(S.x, S.z);
}

// teleports to well-known spots
const TP = [
  // [label, landmark name or [x,z], road name to snap to]
  ['The Cross', null, 'High Street'],
  ['Tewkesbury Abbey', 'Tewkesbury Abbey', 'Church Street'],
  ['Town Hall', 'Tewkesbury Town Hall', 'High Street'],
  ['Morrisons', 'Morrisons', null],
  ['Leisure Centre', 'Tewkesbury Leisure Centre', null],
  ["King John's Bridge", 'bridge:Mythe Road', 'Mythe Road'],
  ['Mythe Bridge (Severn)', 'bridge:Mythe Bridge', 'Mythe Bridge'],
];
function teleport(i) {
  const t = TP[i]; if (!t) return;
  let x = meta.spawn[0], z = meta.spawn[1];
  if (t[1]?.startsWith('bridge:')) { const br = roads.filter(r => r.b && r.n === t[1].slice(7)).sort((a, b) => b.pts.length - a.pts.length)[0]; if (br) { const p = br.pts[br.pts.length >> 1]; x = p[0]; z = p[1]; } }
  else if (t[1]) { const l = landmarks.find(l => l.n === t[1]); if (l) { x = l.x; z = l.z; } }
  const r = (t[2] && nearestRoad(x, z, 300, r => r.n === t[2]))
    || nearestRoad(x, z, 300, r => r.t !== 'track' && r.t !== 'service') || nearestRoad(x, z, 300);
  if (r) respawn(r);
  flash(t[0]);
}
document.getElementById('tps').innerHTML = TP.map((t, i) => `${i + 1} ${t[0]}`).join(' · ');

// ------------------------------------------------------------------ audio
let audio = null, osc, osc2, gain, soundAsked = false;
function toggleSound() {
  if (!audio) {
    audio = new AudioContext();
    osc = audio.createOscillator(); osc.type = 'sawtooth';
    osc2 = audio.createOscillator(); osc2.type = 'square';
    const f = audio.createBiquadFilter(); f.type = 'lowpass'; f.frequency.value = 600;
    gain = audio.createGain(); gain.gain.value = 0;
    osc.connect(f); osc2.connect(f); f.connect(gain); gain.connect(audio.destination);
    osc.start(); osc2.start(); sound = true;
  } else { sound = !sound; }
}

// ------------------------------------------------------------------ physics
const WB = 2.64;
function collide() {
  const fx = Math.sin(S.h), fz = Math.cos(S.h);
  let hit = 0;
  for (const off of [1.45, 0, -1.45]) {
    let cx = S.x + fx * off, cz = S.z + fz * off;
    const rad = 0.95;
    const ci = Math.floor(cx / CELL), cj = Math.floor(cz / CELL);
    for (let i = ci - 1; i <= ci + 1; i++) for (let j = cj - 1; j <= cj + 1; j++) {
      const L = segHash.get(hkey(i, j));
      if (L) for (const [ax, az, bx, bz] of L) {
        const [qx, qz] = closestOnSeg(cx, cz, ax, az, bx, bz);
        const dx = cx - qx, dz = cz - qz, d = Math.hypot(dx, dz);
        if (d < rad && d > 1e-6) {
          const nx = dx / d, nz = dz / d, pen = rad - d;
          S.x += nx * pen; S.z += nz * pen; cx += nx * pen; cz += nz * pen;
          const vx = fx * S.v, vz = fz * S.v, vn = vx * nx + vz * nz;
          if (vn < 0) { hit = Math.max(hit, -vn); S.v = ((vx - nx * vn * 1.4) * fx + (vz - nz * vn * 1.4) * fz) * 0.9; }
        }
      }
      const T = treeHash.get(hkey(i, j));
      if (T) for (const [tx, tz, tr] of T) {
        const dx = cx - tx, dz = cz - tz, d = Math.hypot(dx, dz), rr = tr + rad;
        if (d < rr && d > 1e-6) {
          const nx = dx / d, nz = dz / d; S.x += nx * (rr - d); S.z += nz * (rr - d);
          const vn = (fx * nx + fz * nz) * S.v; if (vn < 0) { hit = Math.max(hit, -vn); S.v *= 0.4; }
        }
      }
    }
  }
  return hit;
}

function physics(dt) {
  const up = keys.KeyW || keys.ArrowUp, down = keys.KeyS || keys.ArrowDown;
  const left = keys.KeyA || keys.ArrowLeft, right = keys.KeyD || keys.ArrowRight, hb = keys.Space;
  if (S.sinking > 0) {
    S.sinking += dt; S.v *= 0.9; S.y -= dt * 1.2;
    if (S.sinking > 1.8) respawn({ x: lastSafe.x, z: lastSafe.z, heading: lastSafe.h });
    return;
  }
  const kind = kindAt(S.x, S.z);
  const offroad = kind !== 2 && !onRoad;
  const MAXV = 40, ACC = offroad ? 5 : 8.5, BRK = 16;
  if (up) S.v += (S.v < 0 ? BRK : ACC * (1 - Math.max(0, S.v) / MAXV)) * dt;
  else if (down) S.v -= (S.v > 0 ? BRK : 6) * dt;
  else S.v -= Math.sign(S.v) * Math.min(Math.abs(S.v), (1.2 + Math.abs(S.v) * 0.04) * dt);
  if (S.v < -10) S.v = -10;
  if (offroad) S.v -= Math.sign(S.v) * Math.min(Math.abs(S.v), Math.abs(S.v) * 0.5 * dt);
  if (hb) S.v -= Math.sign(S.v) * Math.min(Math.abs(S.v), 9 * dt);
  const target = (left ? 1 : 0) - (right ? 1 : 0);
  const maxSteer = 0.62 / (1 + Math.abs(S.v) / 14);
  S.steer += (target * maxSteer - S.steer) * Math.min(1, dt * 7);
  const yaw = S.v * Math.tan(S.steer) / WB * (hb ? 1.6 : 1);
  S.h += yaw * dt;
  // slip: lateral velocity builds with handbrake, decays with grip
  S.vl += (-yaw * S.v * (hb ? 0.05 : 0.012)) * dt * 10;
  S.vl *= Math.exp(-dt * (hb ? 1.2 : 6));
  const fx = Math.sin(S.h), fz = Math.cos(S.h);
  S.x += (fx * S.v + fz * S.vl) * dt;
  S.z += (fz * S.v - fx * S.vl) * dt;
  S.x = Math.max(-HALF + 5, Math.min(HALF - 5, S.x)); S.z = Math.max(-HALF + 5, Math.min(HALF - 5, S.z));
  const hit = collide();
  if (hit > 6) { flash('Crunch!', 700); shake = Math.min(1, hit / 20); }
  // ground follow
  const g = driveY(S.x, S.z) + (kind === 2 ? 0.16 : onPavement(S.x, S.z) ? 0.31 : onRoad ? 0.16 : 0);
  if (S.y > g + 0.05) { S.yv -= 9.8 * dt; S.y += S.yv * dt; if (S.y < g) { S.y = g; S.yv = 0; } }
  else { S.y = g; S.yv = 0; }
  const hf = driveY(S.x + fx * 1.4, S.z + fz * 1.4), hr = driveY(S.x - fx * 1.4, S.z - fz * 1.4);
  const hl = driveY(S.x + fz * 0.8, S.z - fx * 0.8), hrr = driveY(S.x - fz * 0.8, S.z + fx * 0.8);
  S.pitch += (Math.atan2(hf - hr, 2.8) - S.pitch) * Math.min(1, dt * 8);
  S.roll += (Math.atan2(hl - hrr, 1.6) + S.vl * 0.02 - S.roll) * Math.min(1, dt * 8);
  if (kind === 1 && !onRoad) { S.sinking = 0.001; flash(waterName(), 1600); }
  safeTimer += dt;
  if (safeTimer > 0.5 && onRoad && kind !== 1 && Math.abs(S.v) > 1) { lastSafe = { x: S.x, z: S.z, h: S.h }; safeTimer = 0; }
}
function waterName() { return 'Splash! Into the river…'; }

// ------------------------------------------------------------------ HUD
const elSpd = document.getElementById('spd'), elStreet = document.getElementById('street'), elLm = document.getElementById('landmark'), elMsg = document.getElementById('msg');
let msgT = 0;
function flash(t, ms = 1400) { elMsg.textContent = t; elMsg.style.opacity = 1; msgT = performance.now() + ms; }
let onRoad = false, hudT = 0, lastStreet = '';
function updateHUD(dt) {
  elSpd.textContent = Math.round(Math.abs(S.v) * 2.23694);
  if (performance.now() > msgT) elMsg.style.opacity = 0;
  hudT += dt; if (hudT < 0.15) return; hudT = 0;
  const r = nearestRoad(S.x, S.z, 25);
  onRoad = !!(r && r.d < r.road.w / 2 + 0.8);
  let label = r && r.d < r.road.w / 2 + 6 ? (r.road.n || ({ service: 'Service road', track: 'Track' }[r.road.t] || 'Unnamed road')) : 'Off road';
  if (r && r.road.r && r.road.n && r.road.n !== r.road.r) label += `<small>${r.road.r}</small>`;
  if (label !== lastStreet) { elStreet.innerHTML = label; lastStreet = label; }
  let best = null, bd = 30 * 30;
  for (const l of landmarks) { const d = (l.x - S.x) ** 2 + (l.z - S.z) ** 2; if (d < bd) { bd = d; best = l; } }
  if (best) { elLm.innerHTML = `${best.n}${best.g ? ` · <b>Grade ${best.g} listed</b>` : ''}${best.m ? ` · ${best.m}` : ''}`; elLm.style.opacity = 1; }
  else elLm.style.opacity = 0;
}

// minimap: pre-render whole town once
const MM = document.createElement('canvas'); const MMS = 2048; MM.width = MM.height = MMS;
{
  const c = MM.getContext('2d'), k = MMS / (2 * HALF), P = (x, z) => [(x + HALF) * k, (z + HALF) * k];
  c.fillStyle = '#7d9a5e'; c.fillRect(0, 0, MMS, MMS);
  c.fillStyle = '#6f9fbf';
  for (const w of water) { c.beginPath(); w.p.forEach(([x, z], i) => c[i ? 'lineTo' : 'moveTo'](...P(x, z))); for (const h of w.h) h.forEach(([x, z], i) => c[i ? 'lineTo' : 'moveTo'](...P(x, z))); c.fill('evenodd'); }
  c.fillStyle = '#c8b494';
  for (const f of foots) { c.beginPath(); f.p.forEach(([x, z], i) => c[i ? 'lineTo' : 'moveTo'](...P(x, z))); c.fill(); }
  c.lineCap = 'round'; c.lineJoin = 'round';
  for (const pass of [0, 1]) for (const r of [...roads].sort((a, b) => (ROAD_RANK[a.t] ?? 2) - (ROAD_RANK[b.t] ?? 2))) {
    c.strokeStyle = pass ? (ROAD_RANK[r.t] >= 5 ? '#f3d27a' : '#ffffff') : '#5b5b5b';
    c.lineWidth = r.w * k + (pass ? 0 : 2);
    c.beginPath(); r.pts.forEach(([x, z], i) => c[i ? 'lineTo' : 'moveTo'](...P(x, z))); c.stroke();
  }
}
const mm = document.getElementById('minimap'), mctx = mm.getContext('2d');
function drawMinimap() {
  const W = mm.width, k = MMS / (2 * HALF);
  const view = bigMap ? 2 * HALF : 380; // metres across
  const s = W / (view * k);
  mctx.save(); mctx.fillStyle = '#6f8c55'; mctx.fillRect(0, 0, W, W);
  const cx = bigMap ? 0 : S.x, cz = bigMap ? 0 : S.z;
  mctx.translate(W / 2, W / 2); mctx.scale(s, s);
  mctx.translate(-(cx + HALF) * k, -(cz + HALF) * k);
  mctx.drawImage(MM, 0, 0);
  mctx.restore();
  // car arrow
  const px = W / 2 + (S.x - cx) * k * s, pz = W / 2 + (S.z - cz) * k * s;
  mctx.save(); mctx.translate(px, pz); mctx.rotate(Math.PI - S.h);
  mctx.fillStyle = '#e8402e'; mctx.strokeStyle = '#fff'; mctx.lineWidth = 3;
  mctx.beginPath(); mctx.moveTo(0, -14); mctx.lineTo(9, 10); mctx.lineTo(0, 5); mctx.lineTo(-9, 10); mctx.closePath(); mctx.fill(); mctx.stroke();
  mctx.restore();
}

// ------------------------------------------------------------------ camera
const camPos = new THREE.Vector3(), camLook = new THREE.Vector3();
let shake = 0, camInit = false;
function updateCamera(dt) {
  const fx = Math.sin(S.h), fz = Math.cos(S.h);
  let want, look;
  if (camMode === 0) {
    const back = 7.5 + Math.abs(S.v) * 0.06;
    want = new THREE.Vector3(S.x - fx * back, S.y + 2.9, S.z - fz * back);
    look = new THREE.Vector3(S.x + fx * 4, S.y + 1.1, S.z + fz * 4);
    want.y = Math.max(want.y, terrainY(want.x, want.z) + 1.5);
  } else if (camMode === 1) {
    want = new THREE.Vector3(S.x + fx * 0.2, S.y + 1.25, S.z + fz * 0.2);
    look = new THREE.Vector3(S.x + fx * 20, S.y + 1.0, S.z + fz * 20);
  } else if (camMode === 4 && window.__pose) {
    // fixed pose (used to line the view up with a real street photo)
    const P = window.__pose;
    want = new THREE.Vector3(P.x, terrainY(P.x, P.z) + (P.h ?? 2.2), P.z);
    look = new THREE.Vector3(P.x + Math.sin(P.yaw) * 20, want.y + Math.tan(P.pitch ?? 0.05) * 20, P.z - Math.cos(P.yaw) * 20);
    camera.fov = P.fov ?? 60; camera.updateProjectionMatrix();
  } else if (camMode === 3) {
    want = new THREE.Vector3(S.x - fx * 110, S.y + 140, S.z - fz * 110);
    look = new THREE.Vector3(S.x + fx * 30, S.y, S.z + fz * 30);
  } else {
    want = new THREE.Vector3(S.x - fx * 24, S.y + 34, S.z - fz * 24);
    look = new THREE.Vector3(S.x + fx * 6, S.y, S.z + fz * 6);
  }
  const k = camMode === 1 || camMode === 4 || !camInit ? 1 : 1 - Math.exp(-dt * 6);
  camPos.lerp(want, k); camLook.lerp(look, camMode === 1 || camMode === 4 || !camInit ? 1 : 1 - Math.exp(-dt * 10));
  camInit = true;
  camera.position.copy(camPos);
  if (shake > 0) { camera.position.x += (Math.random() - 0.5) * shake; camera.position.y += (Math.random() - 0.5) * shake; shake = Math.max(0, shake - dt * 2); }
  camera.lookAt(camLook);
  sun.position.set(S.x + SUN_DIR.x * 300, S.y + SUN_DIR.y * 300, S.z + SUN_DIR.z * 300);
  sun.target.position.set(S.x, S.y, S.z);
  window._sky.position.copy(camera.position);
}

// ------------------------------------------------------------------ loop
document.getElementById('loading').remove();
if (_initRoad) S.h = bestHeading(_initRoad);
const clock = new THREE.Clock();
function frame() {
  const dt = Math.min(0.05, clock.getDelta());
  physics(dt);
  car.position.set(S.x, S.y, S.z);
  car.rotation.set(0, 0, 0); car.rotateY(S.h); car.rotateX(-S.pitch); car.rotateZ(S.roll);
  carBody.rotation.x = -Math.max(-0.04, Math.min(0.04, (keys.KeyW || keys.ArrowUp ? -0.015 : 0) + (keys.KeyS || keys.ArrowDown ? 0.02 : 0)));
  for (let i = 0; i < 4; i++) { wheels[i].children[0].rotation.x += S.v * dt / 0.33; wheels[i].children[1].rotation.x = wheels[i].children[0].rotation.x; if (i < 2) wheels[i].rotation.y = S.steer; }
  updateCamera(dt);
  updateHUD(dt);
  drawMinimap();
  if (audio) {
    const rpm = 0.25 + (Math.abs(S.v) % 11) / 11 * 0.75 + Math.min(1, Math.abs(S.v) / 40) * 0.3;
    osc.frequency.setTargetAtTime(45 + rpm * 110, audio.currentTime, 0.05);
    osc2.frequency.setTargetAtTime(22 + rpm * 55, audio.currentTime, 0.05);
    gain.gain.setTargetAtTime(sound ? 0.05 + (keys.KeyW || keys.ArrowUp ? 0.04 : 0) : 0, audio.currentTime, 0.1);
  }
  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}
frame();
window.__game = { S, scene, camera, teleport, nearestRoad, landmarks, keys, step(dt) { physics(dt); updateHUD(dt); }, set cam(m) { camMode = m; camInit = false; }, get cam() { return camMode; } };
