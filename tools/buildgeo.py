"""Building geometry: LIDAR-faceted roofs with eaves, steps and chimneys; walls split into
ground floor / upper storeys / gable, with window bays aligned per wall and shopfronts on shop streets.

Wall vertices carry a `kind`: 0 = window bay, 1 = plain wall, 2 = shopfront. The game samples a
3-cell texture atlas per wall style using that kind.
"""
import math
import numpy as np
import shapely
from shapely.geometry import Point
from scipy.spatial import Delaunay, cKDTree
from scipy import ndimage

import roofseg

KIND_WINDOW, KIND_PLAIN, KIND_SHOP = 0, 1, 2
OVERHANG = 0.32
LO = 2.3


class Sink:
    """Accumulates wall + roof geometry."""

    def __init__(self, nstyles):
        self.walls = {s: {"pos": [], "uv": [], "col": [], "kind": []} for s in range(nstyles)}
        self.roof = {"pos": [], "uv": [], "col": []}

    def wall_quad(self, style, p0b, p1b, p1t, p0t, uv0b, uv1b, uv1t, uv0t, col, kind):
        W = self.walls[style]
        for p, uv in ((p0b, uv0b), (p1b, uv1b), (p1t, uv1t), (p0b, uv0b), (p1t, uv1t), (p0t, uv0t)):
            W["pos"] += p
            W["uv"] += uv
            W["col"] += col
            W["kind"].append(kind)

    def roof_tri(self, pts, uvs, col):
        for p, uv in zip(pts, uvs):
            self.roof["pos"] += p
            self.roof["uv"] += uv
            self.roof["col"] += col


def edge_points(coords, step=1.0):
    """Densify a closed ring. Returns list of (x, y, edge_index, t, edge_len) for each vertex (no closing dup)."""
    out = []
    for ei, ((x0, y0), (x1, y1)) in enumerate(zip(coords[:-1], coords[1:])):
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 1e-3:
            continue
        n = max(1, int(math.ceil(L / step)))
        for k in range(n):
            t = k / n
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, ei, t, L))
    return out


def build(ctx, poly, bid, base, ipm, use_lidar, eave_guess, style, wc, rc, is_church,
          seg_material, shop_paint, is_shop, sink):
    """Emit one building. Returns dict with eave, storey height, street-facing segments, chimneys."""
    E_MIN, N_MAX, E0, N0 = ctx["E_MIN"], ctx["N_MAX"], ctx["E0"], ctx["N0"]
    dsm_s, sample, dtm = ctx["dsm_s"], ctx["sample"], ctx["dtm"]
    others = ctx["ALL_B"]          # prepared union of all footprints
    road_near = ctx["road_near"]   # (E, N) -> (dist, width, (rx, ry)) or None

    loc = lambda E, N: [E - E0, N0 - N]

    # ---------------------------------------------------------- roof facets
    planes, labels, pix_xy, r0c0, chimneys = None, None, None, None, []
    if use_lidar and ipm is not None and len(ipm[0]) >= 12:
        rows, cols, Ex, Ny = ipm
        H = dsm_s[rows, cols] - base
        seg = roofseg.segment(rows, cols, H)
        if seg is not None:
            labels, planes, r0c0, chimneys = seg
            pix_xy = np.column_stack([Ex, Ny])
            hmax = float(H.max()) + 0.3
    if planes is None:
        planes = np.array([[0.0, 0.0, max(eave_guess, LO)]])
        hmax = max(eave_guess, LO) + 0.01
        cxy = poly.representative_point()
        pix_xy = np.array([[cxy.x, cxy.y]])
        labels = np.array([0])
        r0c0 = (int(N_MAX - cxy.y), int(cxy.x - E_MIN))
    r0, c0 = r0c0
    tree = cKDTree(pix_xy)

    def plane_z(k, E, N):
        x = (E - E_MIN - 0.5) - c0
        y = -((N_MAX - N - 0.5) - r0)
        a, b, c = planes[k]
        return min(max(a * x + b * y + c, LO), hmax)

    def label_at(E, N):
        return int(labels[tree.query((E, N))[1]])

    # ---------------------------------------------------------- outline points
    rings = [list(poly.exterior.coords)] + [list(r.coords) for r in poly.interiors]
    ring_ep = [edge_points(r) for r in rings]
    bpts, bmeta = [], []           # boundary points and (ring, idx)
    over_pts = []
    seg_info = []                  # per ring: list of per-point (outward normal, party?) for segment starting there
    for ri, ep in enumerate(ring_ep):
        info = []
        n = len(ep)
        for i in range(n):
            x0, y0, ei, t, L = ep[i]
            x1, y1 = ep[(i + 1) % n][:2]
            dx, dy = x1 - x0, y1 - y0
            l = math.hypot(dx, dy) or 1.0
            nx, ny = dy / l, -dx / l
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            if poly.contains(Point(mx + nx * 0.2, my + ny * 0.2)):
                nx, ny = -nx, -ny
            party = shapely.contains_xy(others, mx + nx * 0.5, my + ny * 0.5)
            info.append((nx, ny, bool(party)))
            bpts.append((x0, y0))
            bmeta.append((ri, i))
        # overhang ring (exterior only, skip party walls)
        if ri == 0:
            for i in range(n):
                nx, ny, party = info[i]
                if party:
                    continue
                x0, y0 = ep[i][:2]
                x1, y1 = ep[(i + 1) % n][:2]
                for t in (0.0,):
                    over_pts.append((x0 + (x1 - x0) * t + nx * OVERHANG, y0 + (y1 - y0) * t + ny * OVERHANG))
        seg_info.append(info)

    # ---------------------------------------------------------- facet polygons
    # roof area = footprint + eaves overhang where it doesn't run into a neighbour
    nb_idx = ctx["B_TREE"].query(poly.buffer(OVERHANG + 0.5), predicate="intersects")
    neigh = [ctx["B_LIST"][i] for i in nb_idx]
    neigh = [g_ for g_ in neigh if g_.intersection(poly).area < 0.5 * min(g_.area, poly.area)]
    zone = poly.buffer(OVERHANG, join_style="mitre", mitre_limit=2.0)
    if neigh:
        zone = zone.difference(shapely.union_all(neigh))
    roof_area = shapely.union_all([poly, zone]).buffer(0)

    shade = 0.94 + 0.06 * ((sum(map(ord, bid)) % 97) / 97.0)
    rcol = [int(c * shade) for c in rc]
    if len(pix_xy) > 1:
        rows, cols = ipm[0], ipm[1]
        minx, miny, maxx, maxy = roof_area.bounds
        cA, cB = int(math.floor(minx - E_MIN)) - 1, int(math.ceil(maxx - E_MIN)) + 1
        rA, rB = int(math.floor(N_MAX - maxy)) - 1, int(math.ceil(N_MAX - miny)) + 1
        Lr = np.full((rB - rA, cB - cA), -1, np.int32)
        Lr[rows - rA, cols - cA] = labels
        lab_ok = Lr >= 0
        ii = ndimage.distance_transform_edt(~lab_ok, return_distances=False, return_indices=True)
        Lr = Lr[ii[0], ii[1]]
        CC, RR = np.meshgrid(np.arange(cA, cB), np.arange(rA, rB))
        near_area = shapely.contains_xy(roof_area.buffer(1.5), E_MIN + CC + 0.5, N_MAX - RR - 0.5)
        facets = []
        for k in range(len(planes)):
            m = (Lr == k) & near_area
            if not m.any():
                facets.append(None)
                continue
            rr_, cc_ = np.nonzero(m)
            x0_ = E_MIN + cc_ + cA
            y1_ = N_MAX - (rr_ + rA)
            boxes = shapely.box(x0_, y1_ - 1, x0_ + 1, y1_)
            facets.append(shapely.union_all(boxes))
        ks = [k for k, f in enumerate(facets) if f is not None and not f.is_empty]
        cover = shapely.coverage_simplify(np.array([facets[k] for k in ks], dtype=object), 0.75)
        cover = [shapely.make_valid(g_) for g_ in cover]
        # snap vertices shared by facets meeting at a ridge/hip/valley onto the planes' intersection
        owners = {}
        for idx_, g_ in enumerate(cover):
            for xy in shapely.get_coordinates(g_):
                owners.setdefault((round(xy[0], 3), round(xy[1], 3)), set()).add(idx_)
        moved = {}
        for key_, own in owners.items():
            if len(own) < 2:
                continue
            kk = [ks[i] for i in own]
            E, N = key_
            hs_ = [plane_z(k, E, N) for k in kk]
            if max(hs_) - min(hs_) > 1.2:
                continue
            x = (E - E_MIN - 0.5) - c0
            y = -((N_MAX - N - 0.5) - r0)
            A_, B_ = [], []
            for k in kk[1:]:
                da, db, dc = planes[kk[0]] - planes[k]
                A_.append([da, db])
                B_.append(-dc)
            A_, B_ = np.array(A_), np.array(B_)
            if len(kk) == 2:
                da, db = A_[0]
                nn = da * da + db * db
                if nn < 1e-4:
                    continue
                t_ = (B_[0] - (da * x + db * y)) / nn
                nxp, nyp = x + da * t_, y + db * t_
            else:
                sol, *_ = np.linalg.lstsq(A_, B_, rcond=None)
                nxp, nyp = sol
            if math.hypot(nxp - x, nyp - y) > 1.3:
                continue
            moved[key_] = (E + (nxp - x), N + (nyp - y))
        if moved:
            def _mv(arr):
                out = arr.copy()
                for i_ in range(len(arr)):
                    m_ = moved.get((round(arr[i_, 0], 3), round(arr[i_, 1], 3)))
                    if m_ is not None:
                        out[i_] = m_
                return out
            cover = [shapely.make_valid(shapely.transform(g_, _mv)) for g_ in cover]
        facet_geoms = []
        for idx_, g_ in enumerate(cover):
            g2 = g_.intersection(roof_area)
            g2 = shapely.union_all([p_ for p_ in shapely.get_parts(g2) if p_.geom_type == "Polygon" and p_.area > 0.05]) \
                if not g2.is_empty else g2
            if not g2.is_empty:
                facet_geoms.append((ks[idx_], g2))
    else:
        facet_geoms = [(0, roof_area)]

    # emit facet triangles
    for k, g_ in facet_geoms:
        a, b, _ = planes[k]
        s_ = math.hypot(a, b)
        tris = shapely.get_parts(shapely.constrained_delaunay_triangles(g_))
        for t_ in tris:
            co = shapely.get_coordinates(t_)[:3]
            outside = ~shapely.contains_xy(poly, co[:, 0], co[:, 1]) & (shapely.distance(poly, shapely.points(co)) > 0.02)
            P3, UV3 = [], []
            for (E, N), out_ in zip(co, outside):
                z = plane_z(k, E, N) - (0.05 if out_ else 0.0)
                l_ = loc(E, N)
                P3.append([l_[0], base + z, l_[1]])
                if s_ < 0.05:
                    UV3.append([0.02, 0.02])
                else:
                    dx, dy = a / s_, b / s_
                    e_, n_ = E - E0, N - N0
                    UV3.append([(e_ * -dy + n_ * dx) / 2.0, (e_ * dx + n_ * dy) * math.sqrt(1 + s_ * s_) / 2.0])
            sink.roof_tri(P3, UV3, rcol)

    # vertical steps where neighbouring facets sit at different heights
    for i_ in range(len(facet_geoms)):
        ki, gi = facet_geoms[i_]
        for j_ in range(i_ + 1, len(facet_geoms)):
            kj, gj = facet_geoms[j_]
            if not gi.buffer(0.01).intersects(gj):
                continue
            shared = gi.boundary.intersection(gj.boundary)
            for ln in shapely.get_parts(shared):
                if ln.geom_type not in ("LineString", "LinearRing"):
                    continue
                co = shapely.get_coordinates(ln)
                for (Ea, Na), (Eb, Nb) in zip(co[:-1], co[1:]):
                    za_i, zb_i = plane_z(ki, Ea, Na), plane_z(ki, Eb, Nb)
                    za_j, zb_j = plane_z(kj, Ea, Na), plane_z(kj, Eb, Nb)
                    if max(abs(za_i - za_j), abs(zb_i - zb_j)) < 0.15:
                        continue
                    la, lb = loc(Ea, Na), loc(Eb, Nb)
                    L = math.hypot(Eb - Ea, Nb - Na)
                    sink.wall_quad(style, [la[0], base + min(za_i, za_j), la[1]], [lb[0], base + min(zb_i, zb_j), lb[1]],
                                   [lb[0], base + max(zb_i, zb_j), lb[1]], [la[0], base + max(za_i, za_j), la[1]],
                                   [0, 0], [L / 3.2, 0], [L / 3.2, 1], [0, 1], list(wc), KIND_PLAIN)

    # wall tops along the outline follow whichever facet covers each outline point (highest if several)
    nb = len(bpts)
    btop = np.zeros(nb)
    fgeoms = [g_ for _, g_ in facet_geoms]
    ftree = shapely.STRtree(fgeoms)
    for v in range(nb):
        E, N = bpts[v]
        hits = ftree.query(Point(E, N).buffer(0.3), predicate="intersects")
        if len(hits):
            btop[v] = max(plane_z(facet_geoms[h][0], E, N) for h in hits)
        else:
            btop[v] = plane_z(label_at(E, N), E, N)

    # chimneys
    ccol = [int(c * 0.9) for c in (wc if style == 4 else (150, 80, 60))]
    cstyle = 4 if style in (0, 1, 4) else style
    if style != 4:
        ccol = [255, 238, 225]
    for (x, y, size, above, k) in chimneys[:6]:
        E = E_MIN + 0.5 + c0 + x
        N = N_MAX - 0.5 - (r0 - y)
        if not poly.buffer(-0.3).contains(Point(E, N)):
            continue
        zb = base + plane_z(k, E, N) - 0.4
        zt = base + plane_z(k, E, N) + min(above, 2.2)
        h = size / 2
        corners = [(E - h, N - h), (E + h, N - h), (E + h, N + h), (E - h, N + h)]
        for i in range(4):
            (Ea, Na), (Eb, Nb) = corners[i], corners[(i + 1) % 4]
            la, lb = loc(Ea, Na), loc(Eb, Nb)
            sink.wall_quad(cstyle, [la[0], zb, la[1]], [lb[0], zb, lb[1]], [lb[0], zt, lb[1]], [la[0], zt, la[1]],
                           [0, 0], [size / 3.2, 0], [size / 3.2, (zt - zb) / 3], [0, (zt - zb) / 3], ccol, KIND_PLAIN)
        l = [loc(*c_) for c_ in corners]
        sink.roof_tri([[l[0][0], zt, l[0][1]], [l[1][0], zt, l[1][1]], [l[2][0], zt, l[2][1]]], [[0.02, 0.02]] * 3, [70, 60, 55])
        sink.roof_tri([[l[0][0], zt, l[0][1]], [l[2][0], zt, l[2][1]], [l[3][0], zt, l[3][1]]], [[0.02, 0.02]] * 3, [70, 60, 55])

    # ---------------------------------------------------------- walls
    ext_tops = [btop[i] for i, (ri, _) in enumerate(bmeta) if ri == 0 and not seg_info[0][bmeta[i][1]][2]]
    eave = float(np.percentile(ext_tops, 20)) if ext_tops else float(np.min(btop))
    eave = max(eave, LO)
    nfl = max(1, int(round(eave / 2.9)))
    hs = eave / nfl
    street_segs = []
    off = 0
    gf_top = base + hs
    e_line = base + eave
    bands = [(-1e9, e_line, "gf"), (e_line, 1e9, "gable")] if nfl == 1 else \
        [(-1e9, gf_top, "gf"), (gf_top, e_line, "up"), (e_line, 1e9, "gable")]
    for ri, ep in enumerate(ring_ep):
        n = len(ep)
        arr = np.array([(p[0], p[1]) for p in ep])
        g = sample(dtm, arr[:, 0], arr[:, 1]) - 0.4
        tops = np.array([base + btop[off + i] for i in range(n)])
        # per 1 m segment attributes
        segs = []
        for i in range(n):
            j = (i + 1) % n
            x0, y0, ei, t0, Le = ep[i]
            x1, y1 = ep[j][:2]
            nx, ny, party = seg_info[ri][i]
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            s_style, s_col = style, tuple(wc)
            if seg_material is not None:
                sm = seg_material(mx, my)
                if sm is not None:
                    s_style, s_col = sm[0], tuple(sm[1])
            street = False
            if not party and ri == 0:
                rn = road_near(mx, my)
                if rn is not None:
                    d, w, (rx, ry) = rn
                    tox, toy = rx - mx, ry - my
                    tl = math.hypot(tox, toy) or 1
                    street = d < w / 2 + 9 and (tox * nx + toy * ny) / tl > 0.35
            if street:
                street_segs.append((mx, my, nx, ny, Le, ei, ri))
            segs.append((ei, party, s_style, s_col, street))
        # merge consecutive 1 m segments on the same edge with the same attributes while the
        # wall top and ground stay linear (keeps gable apexes, drops redundant vertices)
        i = 0
        while i < n:
            ei, party, s_style, s_col, street = segs[i]
            j = i + 1
            while j < n and segs[j] == segs[i]:
                # candidate run i..j+1: check linearity of interior vertices
                k_end = (j + 1) % n if j + 1 <= n else j
                xa, ya = ep[i][:2]
                xb, yb = ep[(j + 1) % n][:2]
                Lab = math.hypot(xb - xa, yb - ya) or 1
                ok = True
                for k in range(i + 1, j + 1):
                    xk, yk = ep[k % n][:2]
                    tk = math.hypot(xk - xa, yk - ya) / Lab
                    if abs(tops[k % n] - (tops[i] + (tops[(j + 1) % n] - tops[i]) * tk)) > 0.05 or \
                       abs(g[k % n] - (g[i] + (g[(j + 1) % n] - g[i]) * tk)) > 0.08:
                        ok = False
                        break
                if not ok:
                    break
                j += 1
            ia, ib = i, j % n
            x0, y0, _, t0, Le = ep[ia]
            x1, y1 = ep[ib][:2]
            t1 = 1.0 if ep[ib][2] != ei else ep[ib][3]
            nbays = int(round(Le / 3.2))
            bays_ok = Le >= 2.2 and nbays >= 1 and not party and not is_church
            u0, u1 = (t0 * nbays, t1 * nbays) if bays_ok else (t0 * Le / 3.2, t1 * Le / 3.2)
            top0, top1 = tops[ia], tops[ib]
            g0, g1 = float(g[ia]), float(g[ib])
            l0, l1 = loc(x0, y0), loc(x1, y1)
            for lo_b, hi_b, name in bands:
                b0, b1 = max(g0, lo_b), max(g1, lo_b)
                tt0, tt1 = min(top0, hi_b), min(top1, hi_b)
                if tt0 <= b0 + 1e-3 and tt1 <= b1 + 1e-3:
                    continue
                tt0, tt1 = max(tt0, b0), max(tt1, b1)
                if name == "gable" or not bays_ok:
                    kind, col = KIND_PLAIN, list(s_col)
                elif name == "gf" and is_shop and street:
                    kind, col = KIND_SHOP, list(shop_paint)
                else:
                    kind, col = KIND_WINDOW, list(s_col)
                v = lambda y_: (y_ - base) / hs
                sink.wall_quad(s_style, [l0[0], b0, l0[1]], [l1[0], b1, l1[1]], [l1[0], tt1, l1[1]], [l0[0], tt0, l0[1]],
                               [u0, v(b0)], [u1, v(b1)], [u1, v(tt1)], [u0, v(tt0)], col, kind)
            i = j
        off += n
    return {"eave": eave, "hs": hs, "street": street_segs}
