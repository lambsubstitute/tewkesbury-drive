// Street-level detail: OSM benches, bins, post boxes, bus stops, phone boxes, cabinets, signals,
// zebra + pelican crossings, garden walls / fences / hedges, and lamp posts along built-up streets.
import * as THREE from 'three';

const M = (o) => new THREE.MeshStandardMaterial(o);
const MAT = {
  black: M({ color: 0x1c1c1e, roughness: 0.6, metalness: 0.3 }),
  grey: M({ color: 0x6d7175, roughness: 0.6, metalness: 0.4 }),
  wood: M({ color: 0x7a5a3a, roughness: 0.9 }),
  red: M({ color: 0xb3121b, roughness: 0.45, metalness: 0.1 }),
  green: M({ color: 0x2f4a38, roughness: 0.7 }),
  glass: M({ color: 0x9fb6c4, roughness: 0.1, metalness: 0.2, transparent: true, opacity: 0.35 }),
  stone: M({ color: 0xb9ae98, roughness: 0.95 }),
  yellow: M({ color: 0xd8b22a, roughness: 0.7 }),
  lampGlow: M({ color: 0xfff2d0, emissive: 0xffe2a0, emissiveIntensity: 0.6 }),
  beacon: M({ color: 0xffa424, emissive: 0xff8a00, emissiveIntensity: 0.9 }),
  sigRed: M({ color: 0x400000, emissive: 0x000000 }),
  sigGreen: M({ color: 0x22ff66, emissive: 0x18c050, emissiveIntensity: 1.2 }),
  white: M({ color: 0xf2f2ee, roughness: 0.6 }),
  pole: M({ color: 0x5a4632, roughness: 1 }),
  signRed: M({ color: 0xc8102e, roughness: 0.5 }),
  signBlue: M({ color: 0x1d5aa8, roughness: 0.5 }),
  signBack: M({ color: 0x9a9ea2, roughness: 0.6, metalness: 0.5 }),
  hedge: M({ color: 0x3f5f2e, roughness: 1 }),
  brickWall: M({ color: 0x96523c, roughness: 0.95 }),
  fence: M({ color: 0x6b4f35, roughness: 0.95 }),
};

const box = (w, h, d, x = 0, y = 0, z = 0) => { const g = new THREE.BoxGeometry(w, h, d); g.translate(x, y, z); return g; };
const cyl = (r, h, x = 0, y = 0, z = 0, seg = 10, r2 = r) => { const g = new THREE.CylinderGeometry(r, r2, h, seg); g.translate(x, y + h / 2, z); return g; };
const sph = (r, x, y, z) => { const g = new THREE.SphereGeometry(r, 10, 8); g.translate(x, y, z); return g; };

function disc(r, y, _m, dz = 0, seg = 24) { const g = new THREE.CircleGeometry(r, seg); g.translate(0, y, 0.03 + dz); return g; }
function tri(sz, y, dz = 0) {
  const sh = new THREE.Shape([new THREE.Vector2(-sz / 2, -sz * 0.29), new THREE.Vector2(sz / 2, -sz * 0.29), new THREE.Vector2(0, sz * 0.58)]);
  const g = new THREE.ShapeGeometry(sh); g.translate(0, y, 0.03 + dz); return g;
}

// part lists per furniture kind: [geometry, material]; object space +z faces the road
const KINDS = {
  lamp: () => [[cyl(0.07, 6.2, 0, 0, 0, 8, 0.1), MAT.grey], [box(0.06, 0.06, 1.3, 0, 6.15, 0.6), MAT.grey], [box(0.35, 0.12, 0.6, 0, 6.05, 1.2), MAT.grey], [box(0.3, 0.04, 0.5, 0, 5.98, 1.2), MAT.lampGlow]],
  bench: () => [[box(1.7, 0.06, 0.45, 0, 0.45, 0), MAT.wood], [box(1.7, 0.4, 0.05, 0, 0.75, -0.22), MAT.wood],
    [box(0.06, 0.45, 0.45, -0.75, 0.22, 0), MAT.black], [box(0.06, 0.45, 0.45, 0.75, 0.22, 0), MAT.black]],
  bin: () => [[cyl(0.26, 0.95), MAT.black]],
  postbox: () => [[cyl(0.27, 1.25), MAT.red], [cyl(0.3, 0.22, 0, 1.25, 0, 12, 0.28), MAT.red], [box(0.26, 0.03, 0.05, 0, 1.05, 0.26), MAT.black]],
  busstop: () => [[cyl(0.045, 2.9), MAT.grey], [box(0.5, 0.5, 0.04, 0, 2.6, 0), MAT.white], [box(0.42, 0.14, 0.05, 0, 2.72, 0), MAT.red]],
  shelter: () => [[box(3.2, 0.08, 1.5, 0, 2.4, -1.4), MAT.grey], [box(3.2, 2.2, 0.04, 0, 1.2, -2.1), MAT.glass],
    [box(0.04, 2.2, 1.4, -1.58, 1.2, -1.4), MAT.glass], [box(0.04, 2.2, 1.4, 1.58, 1.2, -1.4), MAT.glass], [box(2.2, 0.05, 0.35, 0, 0.5, -1.85), MAT.grey]],
  phone: () => [[box(0.92, 2.5, 0.92, 0, 1.25, 0), MAT.red], [box(0.95, 0.12, 0.95, 0, 2.55, 0), MAT.red],
    [box(0.6, 1.5, 0.94, 0, 1.25, 0), MAT.glass], [box(0.94, 1.5, 0.6, 0, 1.25, 0), MAT.glass], [box(0.7, 0.12, 0.96, 0, 2.3, 0), MAT.white]],
  cabinet: () => [[box(1.0, 1.25, 0.42, 0, 0.62, 0), MAT.green]],
  bollard: () => [[cyl(0.1, 0.9), MAT.black]],
  bikes: () => [[box(0.05, 0.8, 0.7, -0.4, 0.4, 0), MAT.grey], [box(0.05, 0.8, 0.7, 0.4, 0.4, 0), MAT.grey]],
  memorial: () => [[box(1.4, 0.4, 1.4, 0, 0.2, 0), MAT.stone], [cyl(0.32, 3.2, 0, 0.4, 0, 4, 0.42), MAT.stone]],
  grit: () => [[box(0.9, 0.7, 0.6, 0, 0.35, 0), MAT.yellow]],
  // Mapillary-detected extras
  upole: () => [[cyl(0.13, 8.5, 0, 0, 0, 8, 0.15), MAT.pole], [box(1.4, 0.1, 0.1, 0, 8.0, 0), MAT.pole]],
  meter: () => [[cyl(0.05, 1.1), MAT.grey], [box(0.25, 0.4, 0.2, 0, 1.25, 0), MAT.black]],
  sighead: () => [[cyl(0.06, 3.0), MAT.grey], [box(0.32, 0.95, 0.26, 0, 2.55, 0), MAT.black],
    [sph(0.09, 0, 2.86, 0.14), MAT.sigRed], [sph(0.09, 0, 2.55, 0.14), MAT.sigRed], [sph(0.09, 0, 2.24, 0.14), MAT.sigGreen]],
  // traffic signs: grey post, plate faces +z (towards approaching traffic)
  tsign_reg: () => [[cyl(0.04, 2.4), MAT.grey], [disc(0.3, 2.45, MAT.signRed), MAT.signRed], [disc(0.22, 2.45, null, 0.012), MAT.white]],
  tsign_speed: () => [[cyl(0.04, 2.4), MAT.grey], [disc(0.3, 2.45), MAT.signRed], [disc(0.23, 2.45, null, 0.012), MAT.white]],
  tsign_noentry: () => [[cyl(0.04, 2.4), MAT.grey], [disc(0.3, 2.45), MAT.signRed], [box(0.36, 0.08, 0.03, 0, 2.45, 0.02), MAT.white]],
  tsign_stop: () => [[cyl(0.04, 2.3), MAT.grey], [disc(0.32, 2.4, null, 0, 8), MAT.signRed]],
  tsign_warn: () => [[cyl(0.04, 2.3), MAT.grey], [tri(0.75, 2.45), MAT.signRed], [tri(0.55, 2.45, 0.012), MAT.white]],
  tsign_info: () => [[cyl(0.04, 2.2), MAT.grey], [box(0.7, 0.5, 0.03, 0, 2.3, 0), MAT.signBlue], [box(0.72, 0.52, 0.02, 0, 2.3, -0.02), MAT.signBack]],
  // Belisha beacon (zebra) and signal pole (pelican / junction)
  beacon: () => [[cyl(0.05, 2.6), MAT.black], [box(0.1, 1.8, 0.1, 0, 0.9, 0), MAT.white], [sph(0.2, 0, 2.75, 0), MAT.beacon]],
  signal: () => [[cyl(0.06, 3.0), MAT.grey], [box(0.32, 0.95, 0.26, 0, 2.55, 0), MAT.black],
    [sph(0.09, 0, 2.86, 0.14), MAT.sigRed], [sph(0.09, 0, 2.55, 0.14), MAT.sigRed], [sph(0.09, 0, 2.24, 0.14), MAT.sigGreen]],
};

export function buildStreet(scene, data, terrainY) {
  const placed = {};     // kind -> [Matrix4]
  const posts = [];      // collision circles [x, z, r]
  const segs = [];       // collision segments [ax, az, bx, bz]
  const mark = [];       // road marking quads (positions)
  const markY = [];      // yellow markings (bus stop bays)
  const put = (kind, x, z, r, y = terrainY(x, z)) => {
    const m = new THREE.Matrix4().makeRotationY(r);
    m.setPosition(x, y, z);
    (placed[kind] ||= []).push(m);
  };
  const quad = (cx, cz, r, along, across, y, out = mark) => {     // flat road marking quad, `along` aligned with local +z
    const ca = Math.cos(r), sa = Math.sin(r);
    const ax = sa * along / 2, az = ca * along / 2, bx = ca * across / 2, bz = -sa * across / 2;
    const P = [[cx - ax - bx, cz - az - bz], [cx - ax + bx, cz - az + bz], [cx + ax + bx, cz + az + bz], [cx + ax - bx, cz + az - bz]];
    const yy = P.map(([x, z]) => Math.max(y, terrainY(x, z) + 0.19));
    for (const i of [0, 1, 2, 0, 2, 3]) out.push(P[i][0], yy[i], P[i][1]);
  };

  for (const f of data.furniture) {
    const { t, x, z, r } = f;
    if (t === 'zebra' || t === 'pelican' || t === 'signals') {
      // r: local +z along the road; local +x across it
      const cx = Math.cos(r), cz = -Math.sin(r), w = f.w || 7, y = terrainY(x, z) + 0.2;
      if (t === 'zebra') {
        const n = Math.max(5, Math.round(w / 0.55)) | 1;
        for (let i = 0; i < n; i++) { const o = -w / 2 + (i + 0.5) * w / n; if (i % 2 === 0) quad(x + cx * o, z + cz * o, r, 2.6, w / n, y); }
        for (const s of [-1, 1]) { const o = s * (w / 2 + 0.8); put('beacon', x + cx * o, z + cz * o, r); posts.push([x + cx * o, z + cz * o, 0.2]); }
      } else {
        if (t === 'pelican') for (const s of [-1.6, 1.6]) {
          const sx = Math.sin(r) * s, sz = Math.cos(r) * s;
          for (let i = 0; i < 8; i++) { const o = -w / 2 + (i + 0.5) * w / 8; quad(x + sx + cx * o, z + sz + cz * o, r, 0.12, w / 16, y); }
        }
        for (const s of [-1, 1]) { const o = s * (w / 2 + 0.7); put('signal', x + cx * o, z + cz * o, s > 0 ? r + Math.PI : r); posts.push([x + cx * o, z + cz * o, 0.2]); }
      }
      continue;
    }
    if (t === 'busbay') {
      // yellow bus stop box: 13 m along the kerb, 2.4 m wide
      const sx = Math.sin(r), sz = Math.cos(r), cx = Math.cos(r), cz = -Math.sin(r), y = terrainY(x, z) + 0.2, L = 13, W = f.w;
      for (const o of [-W / 2, W / 2]) quad(x + cx * o, z + cz * o, r, L, 0.15, y, markY);
      for (const o of [-L / 2, L / 2]) quad(x + sx * o, z + sz * o, r, 0.15, W, y, markY);
      continue;
    }
    if (t === 'tsign') {
      const k = f.s && KINDS['tsign_' + f.s] ? 'tsign_' + f.s : 'tsign_' + f.c;
      put(k, x, z, r); posts.push([x, z, 0.1]);
      continue;
    }
    if (!KINDS[t]) continue;
    put(t, x, z, r);
    if (t === 'busstop' && f.s) put('shelter', x, z, r);
    if (['lamp', 'postbox', 'phone', 'busstop', 'memorial', 'bollard', 'upole', 'sighead', 'meter'].includes(t)) posts.push([x, z, t === 'phone' ? 0.6 : t === 'memorial' ? 0.8 : 0.25]);
  }

  for (const [kind, mats] of Object.entries(placed)) {
    for (const [geo, mat] of KINDS[kind]()) {
      const im = new THREE.InstancedMesh(geo, mat, mats.length);
      mats.forEach((m, i) => im.setMatrixAt(i, m));
      im.castShadow = kind !== 'bin' && kind !== 'bollard';
      im.receiveShadow = true;
      scene.add(im);
    }
  }

  // boundaries: one stretched box per segment
  const byType = {};
  for (const b of data.barriers) {
    const thick = b.t === 'hedge' ? 0.9 : b.t === 'fence' ? 0.06 : 0.3;
    const mat = b.t === 'hedge' ? MAT.hedge : b.t === 'fence' ? MAT.fence : /stone|limestone/.test(b.m) ? MAT.stone : MAT.brickWall;
    const key = mat.uuid;
    for (let i = 1; i < b.p.length; i++) {
      const [ax, az] = b.p[i - 1], [bx, bz] = b.p[i];
      const len = Math.hypot(bx - ax, bz - az); if (len < 0.05) continue;
      const ya = terrainY(ax, az), yb = terrainY(bx, bz), y0 = Math.min(ya, yb) - 0.3, h = b.h + Math.abs(ya - yb) * 0.5 + 0.3;
      const m = new THREE.Matrix4().compose(new THREE.Vector3((ax + bx) / 2, y0 + h / 2, (az + bz) / 2),
        new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.atan2(bx - ax, bz - az)),
        new THREE.Vector3(thick, h, len + (b.t === 'fence' ? 0 : thick)));
      (byType[key] ||= { mat, list: [] }).list.push(m);
      segs.push([ax, az, bx, bz]);
    }
  }
  const unit = new THREE.BoxGeometry(1, 1, 1);
  for (const { mat, list } of Object.values(byType)) {
    const im = new THREE.InstancedMesh(unit, mat, list.length);
    list.forEach((m, i) => im.setMatrixAt(i, m));
    im.castShadow = im.receiveShadow = true;
    scene.add(im);
  }

  if (markY.length) {
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(markY, 3)); g.computeVertexNormals();
    const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: 0xe8c23a, roughness: 0.7, side: THREE.DoubleSide, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -6, polygonOffsetUnits: -6 }));
    m.renderOrder = 2; scene.add(m);
  }
  if (mark.length) {
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(mark, 3)); g.computeVertexNormals();
    const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: 0xf0eee6, roughness: 0.7, side: THREE.DoubleSide, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -6, polygonOffsetUnits: -6 }));
    m.renderOrder = 2; m.receiveShadow = true;
    scene.add(m);
  }
  return { posts, segs };
}

// ---------------------------------------------------------------- pavements + kerbs
const PAVE_LIFT = 0.31, ROAD_LIFT = 0.15;
function slabTexture() {
  const c = document.createElement('canvas'); c.width = c.height = 256;
  const x = c.getContext('2d');
  x.fillStyle = '#8f8c86'; x.fillRect(0, 0, 256, 256);                      // joints
  for (let r = 0; r < 4; r++) for (let q = 0; q < 4; q++) {
    const v = 176 + (Math.random() * 26 | 0), tint = Math.random() * 6 | 0;
    x.fillStyle = `rgb(${v + tint},${v + tint - 2},${v - 4})`;
    x.fillRect(q * 64 + 1.5, r * 64 + 1.5, 61, 61);
  }
  for (let i = 0; i < 2600; i++) { const v = Math.random() * 255 | 0; x.fillStyle = `rgba(${v},${v},${v},.07)`; x.fillRect(Math.random() * 256, Math.random() * 256, 2, 2); }
  for (let i = 0; i < 6; i++) { x.fillStyle = 'rgba(60,55,50,.10)'; x.beginPath(); x.arc(Math.random() * 256, Math.random() * 256, 4 + Math.random() * 10, 0, 7); x.fill(); }
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.wrapS = t.wrapT = THREE.RepeatWrapping; t.anisotropy = 8;
  return t;
}

export function buildPavements(scene, tris, kerbs, terrainY) {
  // walking surface (2.4 m slab texture repeat, i.e. 600 mm flags)
  const n = tris.length / 2, pos = new Float32Array(n * 3), uv = new Float32Array(n * 2);
  for (let i = 0; i < n; i++) {
    const x = tris[i * 2], z = tris[i * 2 + 1];
    pos[i * 3] = x; pos[i * 3 + 1] = terrainY(x, z) + PAVE_LIFT; pos[i * 3 + 2] = z;
    uv[i * 2] = x / 2.4; uv[i * 2 + 1] = z / 2.4;
  }
  for (let t = 0; t < n * 3; t += 9) {        // all triangles face up
    const ux = pos[t + 3] - pos[t], uz = pos[t + 5] - pos[t + 2], vx = pos[t + 6] - pos[t], vz = pos[t + 8] - pos[t + 2];
    if (uz * vx - ux * vz < 0) for (let k = 0; k < 3; k++) {
      const a = pos[t + 3 + k]; pos[t + 3 + k] = pos[t + 6 + k]; pos[t + 6 + k] = a;
      if (k < 2) { const tu = t / 3 * 2; const b = uv[tu + 2 + k]; uv[tu + 2 + k] = uv[tu + 4 + k]; uv[tu + 4 + k] = b; }
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  g.computeVertexNormals();
  const pm = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ map: slabTexture(), roughness: 0.92, side: THREE.DoubleSide }));
  pm.receiveShadow = true; scene.add(pm);

  // kerb stones: vertical face from carriageway to pavement + a 150 mm granite top strip
  const kp = [];
  for (let i = 0; i < kerbs.length; i += 4) {
    const ax = kerbs[i], az = kerbs[i + 1], bx = kerbs[i + 2], bz = kerbs[i + 3];
    const ya = terrainY(ax, az), yb = terrainY(bx, bz);
    kp.push(ax, ya + ROAD_LIFT, az, bx, yb + ROAD_LIFT, bz, bx, yb + PAVE_LIFT + 0.005, bz,
            ax, ya + ROAD_LIFT, az, bx, yb + PAVE_LIFT + 0.005, bz, ax, ya + PAVE_LIFT + 0.005, az);
  }
  const kg = new THREE.BufferGeometry(); kg.setAttribute('position', new THREE.Float32BufferAttribute(kp, 3)); kg.computeVertexNormals();
  const km = new THREE.Mesh(kg, new THREE.MeshStandardMaterial({ color: 0xb9b6ae, roughness: 0.8, side: THREE.DoubleSide }));
  km.receiveShadow = true; scene.add(km);

  // 1 m raster of where the pavement is, so the car rides up onto it
  const S = 2500, cv = document.createElement('canvas'); cv.width = cv.height = S;
  const cx = cv.getContext('2d'); cx.fillStyle = '#fff';
  for (let i = 0; i < tris.length; i += 6) {
    cx.beginPath(); cx.moveTo(tris[i] + S / 2, tris[i + 1] + S / 2); cx.lineTo(tris[i + 2] + S / 2, tris[i + 3] + S / 2); cx.lineTo(tris[i + 4] + S / 2, tris[i + 5] + S / 2); cx.fill();
  }
  const img = cx.getImageData(0, 0, S, S).data, mask = new Uint8Array(S * S);
  for (let i = 0; i < S * S; i++) mask[i] = img[i * 4 + 3] > 127 ? 1 : 0;
  return (x, z) => { const i = Math.floor(x + S / 2), j = Math.floor(z + S / 2); return i >= 0 && j >= 0 && i < S && j < S && mask[j * S + i] === 1; };
}
