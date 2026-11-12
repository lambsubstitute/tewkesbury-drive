"""Roof segmentation from a LIDAR surface model.

Region-grows planar facets over the 1 m DSM pixels of one building, so terraces mapped as a single
OSM outline still get one facet per roof slope, steps between neighbours stay vertical, and small
spikes become chimneys instead of lumps.
"""
from collections import deque
import numpy as np
from scipy import ndimage

RES_TOL = 0.35      # m: pixel joins a facet if within this of the facet plane
SLOPE_TOL = 0.35    # gradient difference allowed while growing
MIN_REGION = 10     # pixels
FLAT_SLOPE = 0.08   # below this gradient a facet is snapped to flat


def _fit(xs, ys, zs):
    A = np.column_stack([xs, ys, np.ones(len(xs))])
    p, *_ = np.linalg.lstsq(A, zs, rcond=None)
    return p


def segment(rows, cols, H):
    """rows/cols: pixel indices; H: height above building base per pixel.
    Returns (labels per pixel, planes[K,3] in local pixel coords x=col, y=-row, chimneys list)."""
    r0, c0 = rows.min(), cols.min()
    hgt, wid = rows.max() - r0 + 1, cols.max() - c0 + 1
    R, C = rows - r0, cols - c0
    if hgt < 3 or wid < 3:
        return None
    Z = np.full((hgt, wid), np.nan, np.float64)
    Z[R, C] = H
    valid = ~np.isnan(Z)
    # fill outside with nearest valid value so gradients near edges are sane
    idx = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
    Zf = Z[idx[0], idx[1]]
    Zs = ndimage.gaussian_filter(Zf, 0.7)
    gr, gc = np.gradient(Zs)
    GX, GY = gc, -gr                    # dz/dx (east), dz/dy (north)
    curv = np.abs(ndimage.laplace(ndimage.uniform_filter(Zf, 3)))
    XX, YY = np.meshgrid(np.arange(wid, dtype=np.float64), -np.arange(hgt, dtype=np.float64))

    lab = np.full((hgt, wid), -1, np.int32)
    seeded = np.zeros((hgt, wid), bool)
    planes = []
    order = np.argsort(np.where(valid, curv, np.inf), axis=None)
    nb4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
    for flat in order:
        r, c = divmod(int(flat), wid)
        if not valid[r, c]:
            break
        if lab[r, c] != -1 or seeded[r, c]:
            continue
        seeded[r, c] = True
        r_lo, r_hi, c_lo, c_hi = max(0, r - 1), min(hgt, r + 2), max(0, c - 1), min(wid, c + 2)
        win = valid[r_lo:r_hi, c_lo:c_hi] & (lab[r_lo:r_hi, c_lo:c_hi] == -1)
        if win.sum() >= 5:
            a, b, cc = _fit(XX[r_lo:r_hi, c_lo:c_hi][win], YY[r_lo:r_hi, c_lo:c_hi][win], Z[r_lo:r_hi, c_lo:c_hi][win])
        else:
            a, b = GX[r, c], GY[r, c]
            cc = Z[r, c] - a * XX[r, c] - b * YY[r, c]
        k = len(planes)
        lab[r, c] = k
        members = [(r, c)]
        q = deque([(r, c)])
        added = 0
        while q:
            pr, pc = q.popleft()
            for dr, dc in nb4:
                nr, nc = pr + dr, pc + dc
                if nr < 0 or nc < 0 or nr >= hgt or nc >= wid or not valid[nr, nc] or lab[nr, nc] != -1:
                    continue
                if abs(a * XX[nr, nc] + b * YY[nr, nc] + cc - Z[nr, nc]) > RES_TOL:
                    continue
                if abs(GX[nr, nc] - a) + abs(GY[nr, nc] - b) > SLOPE_TOL * 1.4:
                    continue
                lab[nr, nc] = k
                members.append((nr, nc))
                q.append((nr, nc))
                added += 1
                if added % 12 == 0 and len(members) >= 6:
                    m = np.array(members)
                    a, b, cc = _fit(XX[m[:, 0], m[:, 1]], YY[m[:, 0], m[:, 1]], Z[m[:, 0], m[:, 1]])
        if len(members) < MIN_REGION:
            for (mr, mc) in members:
                lab[mr, mc] = -1      # free again: another facet may still grow over them
            planes.append(None)
            continue
        m = np.array(members)
        planes.append(_fit(XX[m[:, 0], m[:, 1]], YY[m[:, 0], m[:, 1]], Z[m[:, 0], m[:, 1]]))

    good = [i for i, p in enumerate(planes) if p is not None]
    if not good:
        return None
    remap = {g: i for i, g in enumerate(good)}
    P = np.array([planes[g] for g in good])
    L = np.full((hgt, wid), -1, np.int32)
    for g, i in remap.items():
        L[lab == g] = i

    def plane_h(k, rr, cc_):
        return P[k, 0] * XX[rr, cc_] + P[k, 1] * YY[rr, cc_] + P[k, 2]

    # merge adjacent facets that are really the same plane
    for _ in range(2):
        changed = False
        pairs = set()
        for dr, dc in ((0, 1), (1, 0)):
            A0 = L[:hgt - dr, :wid - dc]
            A1 = L[dr:, dc:]
            m = (A0 >= 0) & (A1 >= 0) & (A0 != A1)
            for i, j in zip(A0[m], A1[m]):
                pairs.add((min(i, j), max(i, j)))
        parent = list(range(len(P)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for i, j in pairs:
            if abs(P[i, 0] - P[j, 0]) + abs(P[i, 1] - P[j, 1]) > 0.2:
                continue
            mj = L == j
            rr, cc_ = np.nonzero(mj)
            if np.mean(np.abs(plane_h(i, rr, cc_) - Z[rr, cc_])) < 0.3:
                parent[find(j)] = find(i)
                changed = True
        if not changed:
            break
        roots = sorted(set(find(i) for i in range(len(P))))
        rmap = {r: n for n, r in enumerate(roots)}
        L = np.where(L >= 0, np.vectorize(lambda v: rmap[find(v)] if v >= 0 else v)(L), L)
        newP = []
        for n in range(len(roots)):
            rr, cc_ = np.nonzero(L == n)
            newP.append(_fit(XX[rr, cc_], YY[rr, cc_], Z[rr, cc_]))
        P = np.array(newP)

    # tiny facets (< 16 px) fold into the neighbour facet that fits them best
    for k in range(len(P)):
        mk = L == k
        n = int(mk.sum())
        if n == 0 or n >= 16:
            continue
        ring = ndimage.binary_dilation(mk, iterations=1) & (L >= 0) & ~mk
        nl = np.unique(L[ring])
        if len(nl) == 0:
            continue
        rr, cc_ = np.nonzero(mk)
        res = [np.median(np.abs(plane_h(j, rr, cc_) - Z[rr, cc_])) for j in nl]
        j = int(np.argmin(res))
        if res[j] < 0.6:
            L[mk] = nl[j]
    # small leftovers: chimneys if they stick up, else absorbed by best neighbour facet
    chimneys = []
    left = valid & (L < 0)
    comp, nc = ndimage.label(left)
    for ci in range(1, nc + 1):
        rr, cc_ = np.nonzero(comp == ci)
        ring = ndimage.binary_dilation(comp == ci, iterations=1) & (L >= 0)
        nl = np.unique(L[ring])
        if len(nl) == 0:
            continue
        res = [np.median(Z[rr, cc_] - plane_h(k, rr, cc_)) for k in nl]
        kbest = nl[int(np.argmin(np.abs(res)))]
        above = float(np.max(Z[rr, cc_] - plane_h(kbest, rr, cc_)))
        if 1 <= len(rr) <= 10 and 0.7 < above < 4.0 and len(chimneys) < 6:
            chimneys.append((float(np.mean(cc_)), float(-np.mean(rr)), max(0.6, min(1.6, np.sqrt(len(rr)) * 0.8)),
                             above, int(kbest)))
            L[rr, cc_] = kbest
        elif len(rr) >= 8 and np.min(np.abs(res)) > 0.8:
            # a real part of the roof that never grew a facet (rough/complex): give it its own
            p = _fit(XX[rr, cc_], YY[rr, cc_], Z[rr, cc_])
            if np.hypot(p[0], p[1]) > 1.2:
                p = np.array([0.0, 0.0, float(np.median(Z[rr, cc_]))])
            P = np.vstack([P, p])
            L[rr, cc_] = len(P) - 1
        else:
            L[rr, cc_] = kbest
    # any still-unlabelled valid pixel -> nearest labelled
    if np.any(valid & (L < 0)):
        lab_ok = L >= 0
        ii = ndimage.distance_transform_edt(~lab_ok, return_distances=False, return_indices=True)
        fill = L[ii[0], ii[1]]
        L = np.where(valid & (L < 0), fill, L)
    # majority clean-up of ragged facet borders
    for _ in range(2):
        newL = L.copy()
        for k in range(len(P)):
            cnt = ndimage.uniform_filter((L == k).astype(np.float32), 3)
            better = valid & (cnt > 0.55) & (L != k)
            if better.any():
                rr, cc_ = np.nonzero(better)
                ok = np.abs(plane_h(k, rr, cc_) - Z[rr, cc_]) < 0.45
                newL[rr[ok], cc_[ok]] = k
        L = newL
    # split off parts of a facet that it fits badly (facets that leaked over a step)
    for _ in range(2):
        nP = len(P)
        for k in range(nP):
            mk = L == k
            if mk.sum() < 16:
                continue
            rr, cc_ = np.nonzero(mk)
            bad = np.zeros_like(mk)
            bad[rr, cc_] = np.abs(plane_h(k, rr, cc_) - Z[rr, cc_]) > 0.9
            bad = ndimage.binary_opening(bad, np.ones((2, 2), bool))
            comp, ncomp = ndimage.label(bad)
            for ci in range(1, ncomp + 1):
                br, bc = np.nonzero(comp == ci)
                if len(br) < 8:
                    continue
                p = _fit(XX[br, bc], YY[br, bc], Z[br, bc])
                if np.hypot(p[0], p[1]) > 1.2:
                    p = np.array([0.0, 0.0, float(np.median(Z[br, bc]))])
                P = np.vstack([P, p])
                L[br, bc] = len(P) - 1
    # last sweep: fold any facet under 12 px into the neighbour that fits it best, unless it stands far
    # above everything around it (tower tops, big dormers)
    for k in range(len(P)):
        mk = L == k
        n = int(mk.sum())
        if n == 0 or n >= 12:
            continue
        ring = ndimage.binary_dilation(mk, iterations=1) & (L >= 0) & ~mk
        nl = np.unique(L[ring])
        if len(nl) == 0:
            continue
        rr, cc_ = np.nonzero(mk)
        res = [np.median(Z[rr, cc_] - plane_h(j, rr, cc_)) for j in nl]
        j = int(np.argmin(np.abs(res)))
        if abs(res[j]) < 2.5:
            L[mk] = nl[j]
    # final refit + flat snapping
    for k in range(len(P)):
        rr, cc_ = np.nonzero(L == k)
        if len(rr) >= 3:
            p = _fit(XX[rr, cc_], YY[rr, cc_], Z[rr, cc_])
            res = np.abs(p[0] * XX[rr, cc_] + p[1] * YY[rr, cc_] + p[2] - Z[rr, cc_])
            keep = res < 0.4
            if keep.sum() >= 3:
                p = _fit(XX[rr[keep], cc_[keep]], YY[rr[keep], cc_[keep]], Z[rr[keep], cc_[keep]])
            if abs(p[0]) + abs(p[1]) < FLAT_SLOPE:
                p = np.array([0.0, 0.0, float(np.median(Z[rr, cc_]))])
            if np.hypot(p[0], p[1]) > 1.4:  # steeper than ~55 deg is wall/tree junk, not roof: flatten
                p = np.array([0.0, 0.0, float(np.median(Z[rr, cc_]))])
            P[k] = p
    used = np.unique(L[valid])
    rm = np.full(len(P), -1, np.int32)
    rm[used] = np.arange(len(used))
    L = np.where(L >= 0, rm[np.maximum(L, 0)], L)
    P = P[used]
    chimneys = [(x, y, s_, h_, int(rm[k])) for (x, y, s_, h_, k) in chimneys]
    labels = L[R, C]
    return labels, P, (r0, c0), chimneys
