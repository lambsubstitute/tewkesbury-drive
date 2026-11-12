// Facade texture atlases. Each wall style is a 4-cell atlas (1024x256):
//   cell 0 = one window bay (3.2 m wide x one storey), cell 1 = plain wall, cell 2 = shopfront, cell 3 = top-storey window.
// Vertex colours tint the atlas, so walls/infill/fascias are drawn near-white where they should take the
// building's real colour, and dark glass / black timbers stay dark.
import * as THREE from 'three';

const CW = 256, CH = 256;

function noise(x, w, h, n, a) {
  for (let i = 0; i < n; i++) {
    const v = Math.random() * 255 | 0;
    x.fillStyle = `rgba(${v},${v},${v},${a})`;
    x.fillRect(Math.random() * w, Math.random() * h, 2, 2);
  }
}

// ---- backgrounds (draw a full cell at origin)
const bg = {
  render(x) { x.fillStyle = '#f6f4ef'; x.fillRect(0, 0, CW, CH); noise(x, CW, CH, 1600, 0.05); x.fillStyle = 'rgba(0,0,0,.06)'; x.fillRect(0, CH - 10, CW, 10); },
  brick(x) {
    x.fillStyle = '#d6c8b4'; x.fillRect(0, 0, CW, CH);
    const BR = ['#9a4a34', '#a8553a', '#8e4430', '#b0603f', '#94503a', '#a04c36', '#874032', '#a65a40'];
    const ch = 6.4, cw = 17.6;
    for (let r = 0, y = 0; y < CH; r++, y += ch)
      for (let c = -1, xx = (r % 2) * cw / 2 - cw; xx < CW; c++, xx += cw) {
        x.fillStyle = BR[Math.abs(r * 7 + c * 3 + ((r * c) % 5)) % BR.length];
        x.fillRect(xx + 0.7, y + 0.7, cw - 1.4, ch - 1.4);
      }
    noise(x, CW, CH, 3000, 0.06);
  },
  timber(x) {
    x.fillStyle = '#f7f5ef'; x.fillRect(0, 0, CW, CH); noise(x, CW, CH, 900, 0.04);
    x.fillStyle = '#1f1c1a';
    x.fillRect(0, 0, CW, 10); x.fillRect(0, CH - 12, CW, 12); x.fillRect(0, 120, CW, 9);   // sole, mid rail, top plate
    for (let s = 0; s < CW; s += 32) x.fillRect(s, 0, 10, CH);                             // close studding
    x.lineWidth = 9; x.strokeStyle = '#1f1c1a';
    x.beginPath(); x.moveTo(0, 124); x.lineTo(32, 6); x.moveTo(CW, 124); x.lineTo(CW - 32, 6); x.stroke();
  },
  ashlar(x) {
    x.fillStyle = '#f5f1e8'; x.fillRect(0, 0, CW, CH); noise(x, CW, CH, 3000, 0.07);
    x.strokeStyle = 'rgba(90,75,50,.32)'; x.lineWidth = 1.5;
    for (let r = 0; r < 8; r++) {
      const y = r * 32; x.beginPath(); x.moveTo(0, y); x.lineTo(CW, y); x.stroke();
      for (let c = (r % 2) * 32; c < CW; c += 64) { x.beginPath(); x.moveTo(c, y); x.lineTo(c, y + 32); x.stroke(); }
    }
  },
  shed(x) { x.fillStyle = '#f2f2f0'; x.fillRect(0, 0, CW, CH); x.fillStyle = 'rgba(0,0,0,.13)'; for (let i = 0; i < CW; i += 12) x.fillRect(i, 0, 3, CH); },
};

// ---- windows (drawn on top of a background)
function sash(x, { frame = '#f4f2ec', surround = null, arch = null } = {}) {
  const wx = 86, wy = 52, ww = 84, wh = 132;
  if (surround) { x.fillStyle = surround; x.fillRect(wx - 9, wy - 9, ww + 18, wh + 14); }
  if (arch) { x.fillStyle = arch; x.fillRect(wx - 8, wy - 18, ww + 16, 16); }
  x.fillStyle = frame; x.fillRect(wx, wy, ww, wh);
  x.fillStyle = '#3c4954'; x.fillRect(wx + 6, wy + 6, ww - 12, wh - 12);
  const g = x.createLinearGradient(0, wy, 0, wy + wh); g.addColorStop(0, 'rgba(170,195,215,.45)'); g.addColorStop(.5, 'rgba(60,80,95,.1)'); g.addColorStop(1, 'rgba(120,140,160,.25)');
  x.fillStyle = g; x.fillRect(wx + 6, wy + 6, ww - 12, wh - 12);
  x.fillStyle = frame;
  x.fillRect(wx, wy + wh / 2 - 3, ww, 6);                                         // meeting rail
  for (const fx of [1 / 3, 2 / 3]) x.fillRect(wx + ww * fx - 1.5, wy, 3, wh);     // glazing bars 6-over-6
  for (const fy of [1 / 4, 3 / 4]) x.fillRect(wx, wy + wh * fy - 1.5, ww, 3);
  x.fillStyle = '#e9e4da'; x.fillRect(wx - 8, wy + wh, ww + 16, 9);               // sill
}
function casement(x) {
  const wx = 74, wy = 40, ww = 108, wh = 64;
  x.fillStyle = '#1f1c1a'; x.fillRect(wx - 6, wy - 6, ww + 12, wh + 12);
  x.fillStyle = '#46525c'; x.fillRect(wx, wy, ww, wh);
  x.strokeStyle = 'rgba(190,190,180,.55)'; x.lineWidth = 1;
  for (let i = -wh; i < ww; i += 9) { x.beginPath(); x.moveTo(wx + i, wy); x.lineTo(wx + i + wh, wy + wh); x.stroke(); x.beginPath(); x.moveTo(wx + i + wh, wy); x.lineTo(wx + i, wy + wh); x.stroke(); }
  x.fillStyle = '#1f1c1a'; x.fillRect(wx + ww / 3 - 2, wy, 4, wh); x.fillRect(wx + 2 * ww / 3 - 2, wy, 4, wh);
  // lower storey window
  const ly = 150;
  x.fillStyle = '#1f1c1a'; x.fillRect(wx - 6, ly - 6, ww + 12, 72);
  x.fillStyle = '#46525c'; x.fillRect(wx, ly, ww, 60);
  x.fillStyle = '#1f1c1a'; x.fillRect(wx + ww / 2 - 2, ly, 4, 60);
}
function lancet(x) {
  x.fillStyle = '#d9cfb8'; x.beginPath(); x.moveTo(98, 220); x.lineTo(98, 90); x.quadraticCurveTo(128, 28, 158, 90); x.lineTo(158, 220); x.fill();
  x.fillStyle = '#2c343c'; x.beginPath(); x.moveTo(106, 214); x.lineTo(106, 92); x.quadraticCurveTo(128, 42, 150, 92); x.lineTo(150, 214); x.fill();
  x.fillStyle = 'rgba(200,180,120,.25)'; x.fillRect(106, 140, 44, 3); x.fillRect(126, 70, 3, 144);
}
function mullion(x) {
  x.fillStyle = '#ddd3bd'; x.fillRect(66, 46, 124, 140);
  x.fillStyle = '#2c343c'; x.fillRect(74, 54, 50, 124); x.fillRect(132, 54, 50, 124);
  x.fillStyle = '#ddd3bd'; x.fillRect(74, 108, 108, 7);
}
function stripWindow(x) { x.fillStyle = '#3a4650'; x.fillRect(20, 40, CW - 40, 40); x.fillStyle = 'rgba(170,195,215,.35)'; x.fillRect(20, 40, CW - 40, 14); }

// ---- shopfront: fascia + glazing, near-white so the shop's paint colour shows through
function shopfront(x) {
  x.fillStyle = '#ffffff'; x.fillRect(0, 0, CW, CH);
  x.fillStyle = '#f0f0f0'; x.fillRect(0, 0, CW, 54);                         // fascia board
  x.fillStyle = 'rgba(0,0,0,.25)'; x.fillRect(0, 54, CW, 4); x.fillRect(0, 0, CW, 3);
  x.fillStyle = '#ffffff'; x.fillRect(0, 58, 10, CH); x.fillRect(CW - 10, 58, 10, CH);   // pilasters
  // display window
  x.fillStyle = '#1d2329'; x.fillRect(16, 66, 158, 150);
  const g = x.createLinearGradient(16, 66, 174, 216); g.addColorStop(0, 'rgba(190,210,225,.5)'); g.addColorStop(.45, 'rgba(70,85,95,.05)'); g.addColorStop(1, 'rgba(150,170,185,.3)');
  x.fillStyle = g; x.fillRect(16, 66, 158, 150);
  x.fillStyle = 'rgba(255,240,200,.18)'; x.fillRect(24, 150, 140, 60);       // lit display
  x.fillStyle = '#ffffff'; x.fillRect(92, 66, 5, 150); x.fillRect(16, 96, 158, 4);
  x.fillStyle = '#e8e8e8'; x.fillRect(16, 216, 158, 40);                      // stallriser
  // door
  x.fillStyle = '#ffffff'; x.fillRect(180, 66, 66, CH - 66);
  x.fillStyle = '#20262c'; x.fillRect(188, 74, 50, 110);
  x.fillStyle = '#d8d8d8'; x.fillRect(188, 190, 50, 60);
  x.fillStyle = '#c8b070'; x.fillRect(230, 180, 4, 14);
}

// cell 3: top storey - the same window drawn about two-thirds the height (typical Georgian top floor)
function atlas(bgFn, winFn, shopFn = shopfront, topFn = null) {
  const c = document.createElement('canvas'); c.width = CW * 4; c.height = CH;
  const x = c.getContext('2d');
  bgFn(x); winFn(x);
  x.save(); x.translate(CW, 0); bgFn(x); x.restore();
  x.save(); x.translate(CW * 2, 0); shopFn(x); x.restore();
  x.save(); x.translate(CW * 3, 0); bgFn(x);
  if (topFn) topFn(x);
  else {   // scale the window vertically about its sill
    const t = document.createElement('canvas'); t.width = CW; t.height = CH;
    const tx = t.getContext('2d'); winFn(tx);
    x.drawImage(t, 0, 0, CW, CH, 0, CH * 0.28, CW, CH * 0.66);
  }
  x.restore();
  return c;
}

export function makeFacadeAtlases(renderer) {
  const mk = (canvas) => {
    const t = new THREE.CanvasTexture(canvas);
    t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = renderer.capabilities.getMaxAnisotropy();
    t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping;
    return t;
  };
  return [
    mk(atlas(bg.render, x => sash(x, { surround: '#ebe7df' }))),           // 0 render / painted
    mk(atlas(bg.timber, casement)),                                         // 1 timber frame
    mk(atlas(bg.ashlar, lancet, bg.ashlar)),                                // 2 church stone
    mk(atlas(bg.shed, stripWindow, x => { bg.shed(x); x.fillStyle = '#9aa0a4'; x.fillRect(30, 40, 196, 216); })), // 3 shed
    mk(atlas(bg.brick, x => sash(x, { arch: '#c98a68' }))),                // 4 brick
    mk(atlas(bg.ashlar, mullion)),                                          // 5 ashlar
  ];
}

// MeshStandardMaterial that picks the atlas cell from the per-vertex `kind` attribute
export function facadeMaterial(map) {
  const m = new THREE.MeshStandardMaterial({ map, vertexColors: true, roughness: 0.88, side: THREE.DoubleSide });
  m.onBeforeCompile = (sh) => {
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nattribute float kind;\nvarying float vKind;')
      .replace('#include <uv_vertex>', '#include <uv_vertex>\nvKind = kind;');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', '#include <common>\nvarying float vKind;')
      .replace('#include <map_fragment>', `
#ifdef USE_MAP
  vec2 fu = fract(vMapUv);
  vec2 cuv = vec2((fu.x * 0.97 + 0.015 + floor(vKind + 0.5)) / 4.0, fu.y * 0.98 + 0.01);
  vec2 sc = vec2(0.25, 1.0);
  vec4 sampledDiffuseColor = textureGrad(map, cuv, dFdx(vMapUv) * sc, dFdy(vMapUv) * sc);
  diffuseColor *= sampledDiffuseColor;
#endif`);
  };
  return m;
}

// clay/slate tiles, greyscale (tinted per roof); 2 m x 2 m
export function roofTexture(renderer) {
  const c = document.createElement('canvas'); c.width = c.height = 256;
  const x = c.getContext('2d');
  x.fillStyle = '#eeeeee'; x.fillRect(0, 0, 256, 256);
  const rowH = 32;
  for (let r = 0; r < 8; r++) {
    const y = r * rowH, off = (r % 2) * 19;
    for (let i = -1; i < 8; i++) {
      const v = 200 + (Math.random() * 50 | 0);
      x.fillStyle = `rgb(${v},${v},${v})`;
      x.fillRect(i * 38 + off + 1, y + 1, 36, rowH - 2);
    }
    x.fillStyle = 'rgba(0,0,0,.35)'; x.fillRect(0, y + rowH - 5, 256, 5);
  }
  noise(x, 256, 256, 3000, 0.07);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace; t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.anisotropy = renderer.capabilities.getMaxAnisotropy();
  return t;
}
