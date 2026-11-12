"""Scene extraction: image -> scene spec (background, title lines, plates/tapes, ribbons, z-order, shadows).

This is the construction behind the user-accepted 2026-11-08 tape posters, made automatic. Those builds did
not trace pixels; they rebuilt the poster as a few designed objects:

* a native 2-stop gradient background (radial for both posters),
* each title as one live text object whose *ink* is scaled non-uniformly onto the measured ink box,
* each tape as a rotated rectangle with its own fitted live text, rotated as one group,
* each ribbon as a rotated band with a gradient across its width, an end fade and a repeated word,
* an explicit stacking order that interleaves titles and tapes,
* soft shadows from the element's own silhouette (Gaussian convolution, alpha union) as separate images,
* hidden letters completed from what is visible (FONT behind a title, CROSS under a tape).

Everything that was a hand-entered number in those jobs is measured here instead: OCR finds lines, the
colour around a line says whether it sits on a plate, plates are grown along their axis, the stacking order
is read from which colour wins where two elements overlap, and occluded or misread words are chosen by
rendering dictionary candidates and comparing them with the visible ink only.
"""
import math
import re

import cv2
import numpy as np

from .text import NOMINAL, fit_matrix, rasterise

SCHEMA = 'ali-scene/1'

# Shadow components (dx, dy, blur radius, opacity) from the accepted jobs, divided by the element height
# they were tuned on so they scale with the artwork.
#   tape   - yellow tapes 2026-11-08 v3, tape height 83 px: (3,8,4,.30) (3,23,14,.40) (3,42,26,.18)
#   ribbon - blue tapes material-light v2, band height 78 px: (1,4,2,.42) (2,13,7,.48) (3,25,16,.23)
#   title  - mean of yellow titles (h 238: (2,25,14,.43) (2,40,26,.16)) and blue titles (h 128: (2,15,9,.42) (2,26,17,.17))
SHADOW_PRESETS = {
    'tape': [(.036, .096, .048, .30), (.036, .277, .169, .40), (.036, .506, .313, .18)],
    'ribbon': [(.013, .051, .026, .42), (.026, .167, .090, .48), (.038, .320, .205, .23)],
    'title': [(.012, .110, .064, .42), (.012, .185, .120, .165)],
}
# Textile material of the accepted blue ribbons (material-light v2): robust least-squares colour field over this
# basis plus seeded fibre noise. Used when a plate is not explained by a vector gradient.
MATERIAL_BASIS = ['1', 'u', 'u2', 'v', 'v2', 'v3', 'uv', 'exp(-(v/.42)^2)']
MATERIAL_NOISE = {'seed': 1109, 'fine_std_per_rmse': 0.4, 'fibre_gain': 0.32}

_WORD = re.compile(r'[A-Za-z]+')


def dist(a, b):
    return np.sqrt(((np.asarray(a, np.float32) - np.asarray(b, np.float32)) ** 2).sum(-1))


def strip(img, cx, cy, theta, ulo, uhi, vlo, vhi, nearest=False):
    """Sample img in an element frame: u along the element axis (screen angle theta, y down), v across it."""
    w, h = max(1, int(round(uhi - ulo))), max(1, int(round(vhi - vlo)))
    c, s = math.cos(theta), math.sin(theta)
    M = np.array([[c, -s, cx + ulo * c - vlo * s], [s, c, cy + ulo * s + vlo * c]], np.float64)
    flags = (cv2.INTER_NEAREST if nearest else cv2.INTER_LINEAR) | cv2.WARP_INVERSE_MAP
    out = cv2.warpAffine(img, M, (w, h), flags=flags, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    inside = cv2.warpAffine(np.ones(img.shape[:2], np.uint8), M, (w, h), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=0) > 0
    return out, inside


def to_page(cx, cy, theta, u, v):
    c, s = math.cos(theta), math.sin(theta)
    return cx + u * c - v * s, cy + u * s + v * c


def to_local(cx, cy, theta, x, y):
    c, s = math.cos(theta), math.sin(theta)
    return (x - cx) * c + (y - cy) * s, -(x - cx) * s + (y - cy) * c


def rect_mask(shape, cx, cy, theta, w, h):
    pts = [to_page(cx, cy, theta, u, v) for u, v in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]
    m = np.zeros(shape, np.uint8)
    cv2.fillPoly(m, [np.round(np.array(pts) * 8).astype(np.int32)], 1, shift=3)
    return m > 0


# ---------------------------------------------------------------- background

def fit_background(src, mask, samples=40000, seed=0):
    """Two-stop gradient (as Illustrator stores it): radial (origin, length) or linear (angle, start, end).
    Returns spec dict and the model image."""
    from scipy.optimize import minimize
    H, W = mask.shape
    ys, xs = np.nonzero(mask)
    if len(ys) < 50:
        ys, xs = np.mgrid[0:H, 0:W].reshape(2, -1)
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(ys), min(samples, len(ys)), replace=False)
    ys, xs = ys[pick].astype(np.float64), xs[pick].astype(np.float64)
    P = src[ys.astype(int), xs.astype(int)].astype(np.float64)

    def solve(t):
        A = np.stack([1 - t, t], 1)
        coef, *_ = np.linalg.lstsq(A, P, rcond=None)
        return np.abs(A @ coef - P).mean(), coef

    def radial(p):
        return np.clip(np.hypot(xs - p[0], ys - p[1]) / max(p[2], 1), 0, 1)

    def linear(p):
        a = p[0]
        proj = xs * math.cos(a) + ys * math.sin(a)
        return np.clip((proj - p[1]) / (p[2] - p[1] if abs(p[2] - p[1]) > 1 else 1), 0, 1)

    best = None
    diag = math.hypot(W, H)
    for kind, f, inits in (('radial', radial, [(W / 2, H / 2, diag * .5), (W / 2, H * .45, diag * .35), (W / 2, H / 2, diag * .8)]),
                           ('linear', linear, [(math.pi / 2, 0, H), (0, 0, W)])):
        for p0 in inits:
            r = minimize(lambda p: solve(f(p))[0], p0, method='Nelder-Mead', options={'maxiter': 400, 'xatol': .5, 'fatol': .01})
            err, coef = solve(f(r.x))
            if best is None or err < best[0] - 0.05:
                best = (err, kind, r.x, coef)
    err, kind, p, coef = best
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    if kind == 'radial':
        t = np.clip(np.hypot(xx - p[0], yy - p[1]) / p[2], 0, 1)
        spec = {'type': 'radial', 'origin': [round(p[0], 1), round(p[1], 1)], 'length': round(float(p[2]), 1)}
    else:
        proj = xx * math.cos(p[0]) + yy * math.sin(p[0])
        t = np.clip((proj - p[1]) / (p[2] - p[1]), 0, 1)
        spec = {'type': 'linear', 'angle_deg': round(math.degrees(p[0]), 2), 'start': round(p[1], 1), 'end': round(p[2], 1)}
    c0, c1 = np.clip(coef, 0, 255)
    spec['stops'] = [[0, [int(round(v)) for v in c0]], [100, [int(round(v)) for v in c1]]]
    spec['mae'] = round(float(err), 2)
    model = (1 - t)[..., None] * c0 + t[..., None] * c1
    return spec, model.astype(np.float32)


def initial_background_mask(src):
    H, W = src.shape[:2]
    border = np.concatenate([src[0], src[-1], src[:, 0], src[:, -1]]).astype(np.float32)
    ref = np.median(border, 0)
    smooth = cv2.blur(src.astype(np.float32), (9, 9))
    var = cv2.blur((src.astype(np.float32) - smooth) ** 2, (9, 9)).sum(-1)
    return (dist(src, ref) < 70) & (var < 150)


# ---------------------------------------------------------------- lines

def line_geometry(poly):
    p = np.asarray(poly, np.float64)
    top, bot = p[1] - p[0], p[2] - p[3]
    theta = math.atan2(top[1] + bot[1], top[0] + bot[0])
    length = (np.hypot(*top) + np.hypot(*bot)) / 2
    height = (np.hypot(*(p[3] - p[0])) + np.hypot(*(p[2] - p[1]))) / 2
    cx, cy = p.mean(0)
    if abs(theta) < math.radians(3):
        theta = 0.0
    return cx, cy, theta, length, height


def describe_line(src, bg_model, rec):
    """Glyph colour, the colour between the letters and whether the line sits on a plate: a colour that is not
    the background and dominates a thin ring around the OCR box (rotation-safe: the ring is drawn in page space)."""
    cx, cy, theta, L, h = line_geometry(rec['polygon'])
    H, W = src.shape[:2]
    base = {'text': rec['text'], 'conf': rec.get('confidence', 1), 'cx': cx, 'cy': cy, 'theta': theta, 'length': L, 'h': h,
            'surround': None, 'glyph': None, 'on_plate': False}
    poly = np.round(np.asarray(rec['polygon'], np.float32)).astype(np.int32)
    x0, y0 = np.maximum(poly.min(0) - int(h), 0)
    x1, y1 = np.minimum(poly.max(0) + int(h) + 1, [W, H])
    inner = np.zeros((y1 - y0, x1 - x0), np.uint8)
    cv2.fillPoly(inner, [poly - [x0, y0]], 1)
    k = max(3, int(round(.07 * h)))
    ring = cv2.dilate(inner, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))).astype(bool) & ~inner.astype(bool)
    crop = src[y0:y1, x0:x1].astype(np.float32)
    bg_here = np.median(bg_model[y0:y1, x0:x1][inner > 0], 0)
    rpx = crop[ring]
    core_mask = np.zeros_like(inner)
    cv2.fillPoly(core_mask, [poly - [x0, y0]], 1)
    cpx = crop[core_mask > 0]
    if len(rpx) < 20 or len(cpx) < 30:
        return base
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, lab, cen = cv2.kmeans(rpx, 3, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    # a plate may be shaded (lit and shadowed tape): same chromaticity, brightness within a factor 2
    chroma = rpx / (rpx.sum(1, keepdims=True) + 1)
    bright = rpx.sum(1) + 1

    def like(c):
        cc = c / (c.sum() + 1)
        ratio = bright / (c.sum() + 1)
        return (np.abs(chroma - cc).sum(1) < .09) & (ratio > .5) & (ratio < 1.6)

    shares = [like(c).mean() if dist(c, bg_here) > 45 else 0 for c in cen]
    dom = cen[int(np.argmax(shares))]
    share = max(shares)
    sel = dist(cpx, dom) > 60
    far = cpx[sel]
    glyph = None
    if len(far) >= 20:
        # lettering runs along the whole line; an occluding object covers only part of it
        cy_, cx_ = np.nonzero(core_mask > 0)
        uu, _ = to_local(cx - x0, cy - y0, theta, cx_[sel].astype(np.float64), cy_[sel].astype(np.float64))
        bins = np.clip(((uu / L + .5) * 10).astype(int), 0, 9)
        _, fl, fc = cv2.kmeans(far, 2, None, crit, 3, cv2.KMEANS_PP_CENTERS)
        fl = fl.ravel()
        spread = [len(set(bins[fl == i])) + dist(fc[i], dom) / 50 for i in range(2)]
        glyph = np.median(far[fl == int(np.argmax(spread))], 0)
    if share >= .5 and dist(dom, bg_here) > 45 and glyph is not None:
        base.update(surround=dom, glyph=glyph, on_plate=True)
        return base
    _, lab, cen = cv2.kmeans(cpx, 2, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    if dist(cen[0], cen[1]) < 40:
        return base
    g = int(np.argmax([dist(c, bg_here) for c in cen]))
    close = cpx[dist(cpx, cen[g]) < 60]
    if len(close) >= 20:   # lit face of the letters (shadows cast on them are effects, not the fill)
        lum = close.sum(1)
        glyph = np.median(close[lum >= np.median(lum)], 0)
    else:
        glyph = cen[g]
    base.update(surround=cen[1 - g], glyph=glyph)
    return base


def group_titles(lines):
    """Join fragments of one title line that OCR split (e.g. CR + YOSS) - same row, same height, adjacent."""
    lines = sorted(lines, key=lambda l: l['cx'] - l['length'] / 2)
    out = []
    for l in lines:
        for g in out:
            last = g['parts'][-1]
            if (g['theta'] == 0 and l['theta'] == 0 and .6 < l['h'] / last['h'] < 1.6 and abs(l['cy'] - last['cy']) < .25 * last['h']
                    and (l['cx'] - l['length'] / 2) - (last['cx'] + last['length'] / 2) < .6 * last['h']):
                g['parts'].append(l)
                break
        else:
            out.append({'parts': [l], 'theta': l['theta']})
    groups = []
    for g in out:
        ps = g['parts']
        x0 = min(p['cx'] - p['length'] / 2 for p in ps)
        x1 = max(p['cx'] + p['length'] / 2 for p in ps)
        text = ps[0]['text']
        for a, b in zip(ps, ps[1:]):
            gap = (b['cx'] - b['length'] / 2) - (a['cx'] + a['length'] / 2)
            text += (' ' if gap > .25 * a['h'] else '') + b['text']
        glyphs = [p['glyph'] for p in ps if p['glyph'] is not None]
        groups.append({'text': text, 'cx': (x0 + x1) / 2, 'cy': float(np.mean([p['cy'] for p in ps])), 'theta': g['theta'],
                       'length': x1 - x0, 'h': max(p['h'] for p in ps), 'glyph': np.median(glyphs, 0) if glyphs else None,
                       'conf': min(p['conf'] for p in ps), 'fragments': len(ps)})
    return groups


def group_plates(lines, src, bg_model):
    """Lines on a plate that share axis and colour and are joined by non-background pixels form one plate."""
    groups = []
    for l in sorted(lines, key=lambda l: l['length'], reverse=True):
        for g in groups:
            r = g[0]
            if abs(math.sin(l['theta'] - r['theta'])) > math.sin(math.radians(5)) or dist(l['surround'], r['surround']) > 60:
                continue
            u, v = to_local(r['cx'], r['cy'], r['theta'], l['cx'], l['cy'])
            if abs(v) > .6 * r['h']:
                continue
            lo, hi = sorted((0, u))
            img, inside = strip(src, r['cx'], r['cy'], r['theta'], lo, hi + 1, -r['h'] * .3, r['h'] * .3)
            bgs, _ = strip(bg_model, r['cx'], r['cy'], r['theta'], lo, hi + 1, -r['h'] * .3, r['h'] * .3)
            bg_like = (dist(img, bgs) < 30)[inside].mean() if inside.any() else 1
            if bg_like < .3:
                g.append(l)
                break
        else:
            groups.append([l])
    return groups


# ---------------------------------------------------------------- plates

def plate_region(src, lines, bg_model, tol=45):
    """Pixels of the plate under these lines: the connected area, reachable from the ring around the lettering,
    whose colour is within tol of a small palette sampled on that ring (covers lit and shaded parts, gradients)."""
    H, W = src.shape[:2]
    samples, seeds = [], []
    for l in lines:
        for u in np.linspace(-.48, .48, 25) * l['length']:
            for v in (-.57, .57):
                x, y = to_page(l['cx'], l['cy'], l['theta'], u, v * l['h'])
                xi, yi = int(round(x)), int(round(y))
                if 0 <= xi < W and 0 <= yi < H:
                    c = src[yi, xi].astype(np.float32)
                    if dist(c, l['surround']) < 80 and dist(c, bg_model[yi, xi]) > 45:
                        samples.append(c)
                        seeds.append((yi, xi))
    if len(samples) < 4:
        return np.zeros((H, W), bool)
    S = np.array(samples, np.float32)
    k = min(6, len(S))
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, _, pal = cv2.kmeans(S, k, None, crit, 2, cv2.KMEANS_PP_CENTERS)
    img = cv2.GaussianBlur(src, (5, 5), 0).astype(np.float32)
    cand = np.zeros((H, W), bool)
    for c in pal:
        cand |= dist(img, c) < tol
    cand &= dist(img, bg_model) > 30
    cand = cv2.morphologyEx(cand.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, cc = cv2.connectedComponents(cand, 8)
    ids = {cc[y, x] for y, x in seeds} - {0}
    return np.isin(cc, list(ids)) if ids else np.zeros((H, W), bool)


def merge_plates(plates, src, bg_model):
    """Lines of one tape that OCR read apart (FON + POWER around a hidden T) end up inside each other's plate."""
    changed = True
    while changed:
        changed = False
        for i, a in enumerate(plates):
            for j, b in enumerate(plates):
                if i == j or abs(math.sin(a['theta'] - b['theta'])) > math.sin(math.radians(6)):
                    continue
                inside = 0
                for l in b['lines']:
                    u, v = to_local(a['cx'], a['cy'], a['theta'], l['cx'], l['cy'])
                    inside += abs(u) < a['w'] / 2 and abs(v) < a['h'] / 2
                # same band seen on both sides of a title that covers it: collinear, same height and colour
                _, bv = to_local(a['cx'], a['cy'], a['theta'], b['cx'], b['cy'])
                collinear = abs(bv) < .35 * a['h'] and .75 < b['h'] / a['h'] < 1.33 and np.abs(a['surround'] / (a['surround'].sum() + 1) - b['surround'] / (b['surround'].sum() + 1)).sum() < .12
                if inside or collinear:
                    m = measure_plate(src, bg_model, a['lines'] + b['lines'])
                    if m is not None:
                        plates = [p for k, p in enumerate(plates) if k not in (i, j)] + [m]
                        changed = True
                        break
            if changed:
                break
    return plates


def measure_plate(src, bg_model, lines):
    H, W = src.shape[:2]
    region = plate_region(src, lines, bg_model)
    weights = np.array([l['length'] for l in lines])
    theta0 = math.atan2((weights * np.sin([l['theta'] for l in lines])).sum(), (weights * np.cos([l['theta'] for l in lines])).sum())
    text_h = float(np.median([l['h'] for l in lines]))
    glyph = lines[int(np.argmax(weights))]['glyph']
    surround = lines[int(np.argmax(weights))]['surround']
    # keep the part of the region near the lines' axis (a fill can run into a touching plate)
    ys, xs = np.nonzero(region)
    ref = lines[int(np.argmax(weights))]
    u, v = to_local(ref['cx'], ref['cy'], theta0, xs.astype(np.float64), ys.astype(np.float64))
    keep = np.abs(v) < 2.5 * text_h
    xs, ys = xs[keep], ys[keep]
    if len(xs) < 50:
        return None
    # orientation: best of the text angle +-6 deg by thinnest projection (robust to ragged ends)
    best = None
    for d in np.radians(np.linspace(-6, 6, 49)):
        t = theta0 + d
        vv = -(xs - ref['cx']) * math.sin(t) + (ys - ref['cy']) * math.cos(t)
        lo, hi = np.percentile(vv, [1, 99])
        if best is None or hi - lo < best[0]:
            best = (hi - lo, t)
    theta = best[1]
    uu, vv = to_local(ref['cx'], ref['cy'], theta, xs.astype(np.float64), ys.astype(np.float64))
    ulo, uhi = np.percentile(uu, [.2, 99.8])
    vlo, vhi = np.percentile(vv, [.5, 99.5])
    # ends that run into the page edge continue beyond it
    for side in (ulo, uhi):
        x, y = to_page(ref['cx'], ref['cy'], theta, side, (vlo + vhi) / 2)
        if x < 3 or y < 3 or x > W - 4 or y > H - 4:
            if side == ulo:
                ulo -= (vhi - vlo) * 2
            else:
                uhi += (vhi - vlo) * 2
    pcx, pcy = to_page(ref['cx'], ref['cy'], theta, (ulo + uhi) / 2, (vlo + vhi) / 2)
    w, h = float(uhi - ulo), float(vhi - vlo)
    # fill profile across the width, end fade along the length (plate pixels only, lettering excluded)
    img, inside = strip(src, pcx, pcy, theta, -w / 2, w / 2, -h / 2, h / 2)
    reg, _ = strip(region.astype(np.uint8), pcx, pcy, theta, -w / 2, w / 2, -h / 2, h / 2, nearest=True)
    use = (reg > 0) & inside & (dist(img, glyph) > 60)
    imgf = img.astype(np.float32)
    rows = [np.median(imgf[r][use[r]], 0) if use[r].sum() > 5 else None for r in range(img.shape[0])]
    valid = [r for r in rows if r is not None]
    prof = np.array(valid, np.float32)
    trim = max(1, len(prof) // 10)
    core_prof = prof[trim:-trim] if len(prof) > 2 * trim + 2 else prof
    spread = float(max(dist(core_prof, np.median(core_prof, 0))))
    if spread < 25:
        fill = {'type': 'flat', 'color': [int(round(c)) for c in np.median(core_prof, 0)]}
    else:
        stops = []
        for f in (0, .25, .5, .75, 1):
            r = int(round(trim + f * (len(prof) - 1 - 2 * trim)))
            stops.append([int(f * 100), [int(round(c)) for c in prof[r]]])
        fill = {'type': 'linear-across', 'stops': stops}
    fade = None
    lum = np.where(use, imgf.mean(-1), np.nan)
    with np.errstate(all='ignore'):
        col = np.nanmedian(lum, 0) if use.any() else None
    if col is not None and np.isfinite(col).sum() > 20 and fill['type'] != 'flat':
        ok = np.isfinite(col)
        peak = np.nanpercentile(col, 95)
        fade = []
        for f in (0, .28, .5, .72, 1):
            c = f * (len(col) - 1)
            near = ok & (np.abs(np.arange(len(col)) - c) < max(8, len(col) * .06))
            if near.sum() >= 3:
                fade.append([int(f * 100), int(round(float(np.clip(1 - np.median(col[near]) / peak, 0, .9)) * 100))])
        if len(fade) < 3 or max(s[1] for s in fade) < 6:
            fade = None
    return {'cx': pcx, 'cy': pcy, 'theta': theta, 'w': w, 'h': h, 'fill': fill, 'fade': fade, 'glyph': glyph, 'spread': spread,
            'text_h': text_h, 'lines': lines, 'surround': surround, 'region': region}


def material_fit(src, plate, mask):
    """Accepted blue-ribbon material: robust colour field over MATERIAL_BASIS on the plate's own pixels."""
    H, W = src.shape[:2]
    y, x = np.mgrid[0:H, 0:W].astype(np.float64)
    u, v = to_local(plate['cx'], plate['cy'], plate['theta'], x, y)
    u, v = u / max(plate['w'] / 2.8, 1), v / (plate['h'] / 2)
    X = np.stack([np.ones_like(u), u, u * u, v, v * v, v ** 3, u * v, np.exp(-(v / .42) ** 2)], 2)
    glyph = plate['glyph']
    valid = mask & (np.abs(v) < .88)
    if glyph is not None:
        valid &= dist(src, glyph) > 70
    if valid.sum() < 500:
        return None
    xx, yy = X[valid], src[valid].astype(np.float64)
    coef = np.linalg.lstsq(xx, yy, rcond=None)[0]
    err = np.linalg.norm(xx @ coef - yy, axis=1)
    keep = err < np.quantile(err, .88)
    coef = np.linalg.lstsq(xx[keep], yy[keep], rcond=None)[0]
    rmse = float(np.sqrt(np.mean((xx[keep] @ coef - yy[keep]) ** 2)))
    return {'basis': MATERIAL_BASIS, 'u_scale': plate['w'] / 2.8, 'rgb_coefficients': coef.tolist(), 'fit_rgb_rmse': round(rmse, 2),
            'fine_noise_std': round(MATERIAL_NOISE['fine_std_per_rmse'] * rmse, 2), 'fibre_gain': MATERIAL_NOISE['fibre_gain'],
            'seed': MATERIAL_NOISE['seed']}


def vector_fill_error(src, plate, mask):
    model = render_plate_fill(plate, src.shape[:2])
    glyph = plate['glyph']
    sel = mask & (dist(src, glyph) > 70) if glyph is not None else mask
    return float(np.sqrt(((src[sel].astype(np.float32) - model[sel]) ** 2).mean())) if sel.any() else 0.0


def render_plate_fill(plate, shape):
    H, W = shape
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    u, v = to_local(plate['cx'], plate['cy'], plate['theta'], x, y)
    f = plate['fill']
    if f['type'] == 'flat':
        out = np.broadcast_to(np.array(f['color'], np.float32), (H, W, 3)).copy()
    else:
        t = np.clip((v + plate['h'] / 2) / plate['h'] * 100, 0, 100)
        offs = [s[0] for s in f['stops']]
        out = np.stack([np.interp(t, offs, [s[1][c] for s in f['stops']]) for c in range(3)], -1).astype(np.float32)
    if plate.get('fade'):
        t = np.clip((u + plate['w'] / 2) / plate['w'] * 100, 0, 100)
        a = np.interp(t, [s[0] for s in plate['fade']], [s[1] / 100 for s in plate['fade']])
        out *= (1 - a)[..., None]
    return out


# ---------------------------------------------------------------- repeats (ribbons)

def ribbon_repeats(plate):
    """A word repeated along the plate (fragments at the page edge or under titles count): word, pitch, copies."""
    words = [l['text'].strip() for l in plate['lines']]
    V = vocab()
    word = max(set(words), key=lambda w: (sum(x in w for x in words), V.get(w.lower(), 0), len(w)))
    related = [l for l in plate['lines'] if l['text'].strip() and (l['text'].strip() in word)]
    if len(related) < 3 or words.count(word) < 1 or len(word) < 3:
        return None
    centres = []
    for l in related:
        t = l['text'].strip()
        u, _ = to_local(plate['cx'], plate['cy'], plate['theta'], l['cx'], l['cy'])
        if t == word:
            centres.append(u)
            continue
        ch = l['length'] / max(1, len(t))
        start = word.find(t)
        if word.startswith(t):       # prefix: the rest of the word is to the right
            centres.append(u - l['length'] / 2 + ch * len(word) / 2)
        elif word.endswith(t):
            centres.append(u + l['length'] / 2 - ch * len(word) / 2)
        else:
            centres.append(u - l['length'] / 2 - ch * start + ch * len(word) / 2)
    us = np.sort(np.array(centres))
    d = np.diff(us)
    d = d[d > .5 * np.median(d)] if len(d) else d
    if not len(d):
        return None
    base = float(np.min(d))
    pitch = float(np.median([x / round(x / base) for x in d]))
    k = np.round((us - us[0]) / pitch)
    origin = float(np.median(us - k * pitch))
    n0 = int(math.floor((-plate['w'] / 2 - origin) / pitch))
    n1 = int(math.ceil((plate['w'] / 2 - origin) / pitch))
    full = [l for l in plate['lines'] if l['text'].strip() == word]
    return {'word': word, 'pitch': round(pitch, 2), 'origin_u': round(origin, 2), 'n': [n0, n1],
            'line_h': float(np.median([l['h'] for l in full])), 'length': float(np.median([l['length'] for l in full]))}


# ---------------------------------------------------------------- z-order

def above(src, a, b):
    """+1 if element a is drawn above b, -1 if below, 0 if unknown (read from the pixels where both lie)."""
    ov = a['_foot'] & b['_foot']
    if ov.sum() < 30:
        return 0
    if a['kind'] == 'title' and b['kind'] == 'title':
        return 0
    if a['kind'] == 'title':
        return -above(src, b, a)
    if b['kind'] == 'title':
        # plate rows away from the plate's own lettering: title colour there means the title is on top
        sel = ov & ~a['_text_band']
        if sel.sum() < 30:
            sel = ov
        title_px = (dist(src[sel], b['_glyph']) < 60).mean()
        plate_px = a['_plate_px'][sel].mean()
        if title_px > .15 and title_px > plate_px * .35:
            return -1
        return 1 if plate_px > .5 else 0
    pa, pb = a['_plate_px'][ov].mean(), b['_plate_px'][ov].mean()
    if dist(a['_surround'], b['_surround']) > 60 and abs(pa - pb) > .2:
        return 1 if pa > pb else -1
    ga = gb = 0
    if (ov & a['_text_band']).sum() > 30 and a['_glyph'] is not None:
        ga = (dist(src[ov & a['_text_band']], a['_glyph']) < 60).mean()
    if (ov & b['_text_band']).sum() > 30 and b['_glyph'] is not None:
        gb = (dist(src[ov & b['_text_band']], b['_glyph']) < 60).mean()
    if abs(ga - gb) > .1:
        return 1 if ga > gb else -1
    return 0


def stacking_order(elements, relation):
    """Topological order (bottom first). Ties: lower elements on the page go first."""
    n = len(elements)
    edges = {i: set() for i in range(n)}
    indeg = [0] * n
    for i in range(n):
        for j in range(i + 1, n):
            r = relation(i, j)
            lo, hi = (j, i) if r > 0 else (i, j) if r < 0 else (None, None)
            if lo is not None and hi not in edges[lo]:
                edges[lo].add(hi)
                indeg[hi] += 1
    order, done = [], set()
    while len(order) < n:
        ready = [i for i in range(n) if i not in done and indeg[i] == 0]
        if not ready:  # cycle: release the lowest remaining element
            ready = [i for i in range(n) if i not in done]
        i = max(ready, key=lambda k: (elements[k]['cy'], -k))
        order.append(i)
        done.add(i)
        for j in edges[i]:
            indeg[j] -= 1
    return order


# ---------------------------------------------------------------- words

_VOCAB = None


def vocab():
    global _VOCAB
    if _VOCAB is None:
        try:
            from wordfreq import top_n_list, zipf_frequency
            words = [w for w in top_n_list('en', 60000) if w.isalpha()]
            _VOCAB = ({w: zipf_frequency(w, 'en') for w in words})
        except ImportError:
            _VOCAB = {}
    return _VOCAB


def _lev(a, b, cap=2):
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _case(like, w):
    if like.isupper():
        return w.upper()
    if like[:1].isupper():
        return w[:1].upper() + w[1:]
    return w


def word_candidates(word, limit=40):
    """Dictionary words within two edits, and two-word splits (OCR drops spaces: EVERYDETAIL)."""
    V = vocab()
    w = word.lower()
    if not V or not w.isalpha() or V.get(w, 0) >= 3:
        return []
    out = []
    for cand, z in V.items():
        if abs(len(cand) - len(w)) <= 2 and z >= 2.5:
            d = _lev(w, cand)
            if d <= 2:
                out.append((d, -z, cand))
    out.sort()
    cands = [_case(word, c) for _, _, c in out[:limit]]
    # letters hidden under other elements: the OCR string is a subsequence of the word (LLS -> LEVELS)
    if len(w) >= 2:
        sub = []
        for cand, z in V.items():
            if len(w) < len(cand) <= len(w) + 4 and z >= 3 and cand[0] == w[0] or (len(w) < len(cand) <= len(w) + 4 and z >= 3 and cand[-1] == w[-1]):
                it = iter(cand)
                if all(ch in it for ch in w):
                    sub.append((-z, cand))
        sub.sort()
        cands += [_case(word, c) for _, c in sub[:limit] if _case(word, c) not in cands]
    for i in range(2, len(w) - 1):
        a, b = w[:i], w[i:]
        if a in V and b in V and V[a] >= 3 and V[b] >= 3:
            cands.append(_case(word, a) + ' ' + _case(word[i:], b))
    return cands


# ---------------------------------------------------------------- text fitting

def _components_in_band(G, v_centre, half):
    count, cc, stats, cent = cv2.connectedComponentsWithStats(G.astype(np.uint8), 8)
    keep = np.zeros(count, bool)
    for c in range(1, count):
        if stats[c, 4] >= 6 and abs(cent[c][1] - v_centre) <= half:
            keep[c] = True
    return keep[cc]


def _ink_box(G, by_rows=True):
    """Ink box of the lettering: rows carrying a real share of the ink (drops shadows and specks), then columns.
    by_rows=False: plain percentiles (titles cut by tapes have rows with little visible ink)."""
    rows = G.sum(1)
    if rows.sum() < 20:
        return None
    if not by_rows:
        ys, xs = np.nonzero(G)
        return (float(np.percentile(xs, .3)), float(np.percentile(ys, .3)), float(np.percentile(xs, 99.7)) + 1, float(np.percentile(ys, 99.7)) + 1)
    c = int(np.argmax(rows))
    top = bot = c
    while top - 1 >= 0 and rows[top - 1] > .05 * rows.max():
        top -= 1
    while bot + 1 < len(rows) and rows[bot + 1] > .05 * rows.max():
        bot += 1
    sub = G[top:bot + 1]
    ys, xs = np.nonzero(sub)
    if len(xs) < 20:
        return None
    return (float(np.percentile(xs, .3)), float(top), float(np.percentile(xs, 99.7)) + 1, float(bot) + 1)


def _score(font, text, box, G, V, tracking=0.0):
    x0, y0, x1, y1 = box
    try:
        m = fit_matrix(font, text, (x0, y0, x1 - x0, y1 - y0), 0, tracking)
    except (ValueError, ZeroDivisionError, TypeError):
        return -1, None
    R = rasterise(font, text, NOMINAL, m, G.shape, tracking=tracking)
    u = ((R | G) & V).sum()
    return ((R & G & V).sum() / u if u else 0), m


TRACKINGS = (0.0, .03, .06, .1, .15)
TIE_IOU = .05
CONTEXT = {'words': set()}


def in_context(cand):
    """A design-domain word on a poster whose other words are design words too."""
    words = {w.lower() for w in cand.split(' ')}
    return bool(words & DESIGN_TERMS) and len(CONTEXT['words'] & DESIGN_TERMS) >= 2


TRACKING_COST = .1   # IoU per unit of tracking (0.15 tracking needs +0.015 IoU)
# words of the graphic-design domain this tool serves: preferred when hidden letters leave a tie
DESIGN_TERMS = set('''font fonts type typeface typography letter letters lettering word words text design designer layout grid poster
posters print ink paper colour color line lines tape tapes kerning tracking leading serif sans bold italic weight glyph glyphs
headline title caption logo brand style styles vector pixel image art artwork page cover label sign'''.split())


def fit_line(text, G, V, fonts, box):
    """Best (iou, font, text, box, m) for the visible glyph mask G (visible area V) in a local frame.
    An end hidden by another element lets the box grow; misread or hidden words are replaced by dictionary
    candidates when the visible ink fits them clearly better."""
    H, W = G.shape
    x0, y0, x1, y1 = box
    band = slice(int(max(0, y0)), int(min(H, y1)))

    def hidden(cols):
        cols = [c for c in cols if 0 <= c < W]
        return bool(cols) and (~V[band][:, cols]).mean() > .5

    hl, hr = hidden(range(int(x0) - 4, int(x0))), hidden(range(int(x1), int(x1) + 4))

    def best_box(font, t):
        cands = [box]
        if hl or hr:
            for k in (1.08, 1.16, 1.25, 1.35, 1.5):
                w = (x1 - x0) * k
                if hl:
                    cands.append((x1 - w, y0, x1, y1))
                if hr:
                    cands.append((x0, y0, x0 + w, y1))
        best = (-1, None, None, 0.0)
        adj = -1
        for tr in (TRACKINGS if ' ' not in t or len(t) > 1 else (0.0,)):
            for b in cands:
                s, m = _score(font, t, b, G, V, tr)
                # letter-spacing must earn its place: designs set most lines at the face's own spacing
                if s - TRACKING_COST * tr > adj:
                    adj, best = s - TRACKING_COST * tr, (s, b, m, tr)
        return best

    usable = [f for f in fonts if f.covers(text.replace(' ', '') or 'x')]
    if not usable:
        return None
    results = []
    for f in usable:
        s, b, m, tr = best_box(f, text)
        results.append((s, f, text, b, m, tr))
    results.sort(key=lambda r: -r[0])
    s, font, t, b, m, tr = results[0]
    # dictionary correction, one word at a time
    tokens = t.split(' ')
    for i, tok in enumerate(list(tokens)):
        scored = []
        for cand in word_candidates(tok):
            trial = ' '.join(tokens[:i] + [cand] + tokens[i + 1:])
            if not font.covers(trial.replace(' ', '')):
                continue
            r = best_box(font, trial)
            if r[0] > s + .015:
                prior = .01 * sum(vocab().get(w.lower(), 0) for w in cand.split(' ')) / len(cand.split(' '))
                scored.append((r, trial, prior, in_context(cand)))
        if scored:
            # letters under other objects leave readings the visible ink cannot tell apart (within TIE_IOU):
            # among those, a word of the poster's own subject wins, then the more common word
            top = max(x[0][0] for x in scored)
            tied = [x for x in scored if x[0][0] >= top - TIE_IOU]
            r, t, _, _ = max(tied, key=lambda x: (x[3], x[0][0] + x[2]))
            s, b, m, tr = r
            tokens = t.split(' ')
    if t != text:
        for f in usable:
            if f is font:
                continue
            s2, b2, m2, tr2 = best_box(f, t)
            if s2 > s:
                s, font, b, m, tr = s2, f, b2, m2, tr2
    return {'iou': round(float(s), 3), 'font': font, 'text': t, 'box': [round(v, 2) for v in b], 'm': m, 'ocr_text': text, 'tracking': tr,
            'hidden_ends': [hl, hr]}


def page_matrix(m, cx, cy, theta, ulo, vlo):
    a, b, c, d, e, f = m
    co, si = math.cos(theta), math.sin(theta)
    ex, ey = to_page(cx, cy, theta, e + ulo, f + vlo)
    return (co * a - si * b, si * a + co * b, co * c - si * d, si * c + co * d, ex, ey)


# ---------------------------------------------------------------- whole scene

def extract(src, records, fonts, ocr=None, log=print, debug_dir=None, refine=True):
    H, W = src.shape[:2]
    CONTEXT['words'] = {re.sub('[^a-z]', '', w.lower()) for r in records for w in r['text'].split()} - {''}
    bg_spec, bg_model = fit_background(src, initial_background_mask(src))
    bg_spec, bg_model = fit_background(src, dist(src, bg_model) < 22)
    lines = [describe_line(src, bg_model, r) for r in records if r['text'].strip() and r.get('confidence', 1) >= .5]
    titles = group_titles([l for l in lines if not l['on_plate']])
    plates = [measure_plate(src, bg_model, g) for g in group_plates([l for l in lines if l['on_plate']], src, bg_model)]
    plates = [p for p in plates if p is not None]
    for l in [l for l in lines if l['on_plate'] and not any(l in p['lines'] for p in plates)]:
        for k, p in enumerate(plates):
            u, v = to_local(p['cx'], p['cy'], p['theta'], l['cx'], l['cy'])
            if abs(u) < p['w'] / 2 and abs(v) < p['h'] / 2:
                plates[k] = measure_plate(src, bg_model, p['lines'] + [l]) or p
                break
    plates = merge_plates(plates, src, bg_model)
    log('scene: %d title lines, %d plates' % (len(titles), len(plates)))

    def plate_masks(p):
        foot = rect_mask((H, W), p['cx'], p['cy'], p['theta'], p['w'], p['h'])
        band = rect_mask((H, W), p['cx'], p['cy'], p['theta'], p['w'], min(p['h'], p['text_h'] * 1.15))
        return foot, band

    for p in plates:
        p['_foot'], p['_text_band'] = plate_masks(p)
    text_bands = np.zeros((H, W), bool)
    for p in plates:
        text_bands |= p['_text_band']

    def title_ink(t):
        L, h = t['length'] * 1.06, t['h'] * 1.1
        img, inside = strip(src, t['cx'], t['cy'], t['theta'], -L / 2, L / 2, -h / 2, h / 2)
        tb, _ = strip(text_bands.astype(np.uint8), t['cx'], t['cy'], t['theta'], -L / 2, L / 2, -h / 2, h / 2, nearest=True)
        bgl, _ = strip(bg_model, t['cx'], t['cy'], t['theta'], -L / 2, L / 2, -h / 2, h / 2)
        G = (dist(img, t['glyph']) < np.minimum(dist(img, bgl), 110)) & inside & (tb == 0)
        G = _components_in_band(G, img.shape[0] / 2, t['h'] * .33)
        return G, -L / 2, -h / 2

    # OCR misses words cut by several tapes (LEVELS): read again on a picture of the title colour alone
    if ocr is not None and titles and any(t['glyph'] is not None for t in titles):
        tc = np.median([t['glyph'] for t in titles if t['glyph'] is not None], 0)
        known = np.zeros((H, W), bool)
        for t in titles:
            known |= rect_mask((H, W), t['cx'], t['cy'], t['theta'], t['length'], t['h'] * .8)
        for p in plates:
            known |= p['_foot']
        ink = (dist(src, tc) < 60) & ~known
        if ink.sum() > 200:
            pics = []
            plates_foot = np.zeros((H, W), bool)
            for p in plates:
                plates_foot |= p['_foot']
            span = int(max([p['h'] for p in plates] + [10]) * 2.2) | 1
            # strokes cut by a tape continue underneath it: bridge them vertically inside tape footprints only
            bridged = ink | (cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((span, 1), np.uint8)).astype(bool) & plates_foot)
            for m in (ink, bridged):
                pic = np.full((H, W, 3), 255, np.uint8)
                pic[m] = 0
                pics.append(pic)
            if debug_dir is not None:
                from PIL import Image
                for q, pic in enumerate(pics):
                    Image.fromarray(pic).save('%s/second-pass-%d.png' % (debug_dir, q))
            cand = [r for rs in ocr(pics) for r in rs if r.get('confidence', 0) >= .5 and len(r['text'].strip()) >= 2]
            # one reading per place: the longest, then the most confident
            cand.sort(key=lambda r: (-len(r['text'].strip()), -r['confidence']))
            extra, taken = [], np.zeros((H, W), bool)
            for r in cand:
                pm = np.zeros((H, W), np.uint8)
                cv2.fillPoly(pm, [np.round(np.asarray(r['polygon'])).astype(np.int32)], 1)
                pm = pm.astype(bool)
                if (pm & taken).sum() < .3 * pm.sum():
                    extra.append(r)
                    taken |= pm
            new = [describe_line(src, bg_model, r) for r in extra]
            new = [dict(l, glyph=tc, on_plate=False) for l in new if l['h'] > 12]
            if new:
                titles += group_titles(new)
                log('scene: second OCR pass found %s' % [l['text'] for l in new])

    elements = []
    for t in titles:
        if t['glyph'] is None:
            continue
        G, ulo, vlo = title_ink(t)
        box = _ink_box(G, by_rows=False)
        if box is None:
            continue
        x0, y0, x1, y1 = box
        foot = np.zeros((H, W), bool)
        pts = [to_page(t['cx'], t['cy'], t['theta'], ulo + x, vlo + y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        cv2.fillPoly(foot.view(np.uint8), [np.round(np.array(pts)).astype(np.int32)], 1)
        elements.append({'kind': 'title', 'line': t, 'cy': t['cy'], '_foot': foot, '_glyph': t['glyph'], '_text_band': np.zeros((H, W), bool),
                         '_surround': None, '_plate_px': None})
    for p in plates:
        plate_px = p['region'] & p['_foot']
        rep = ribbon_repeats(p)
        kind = 'ribbon' if rep else 'tape'
        elements.append({'kind': kind, 'plate': p, 'repeat': rep, 'cy': p['cy'], '_foot': p['_foot'], '_glyph': p['glyph'],
                         '_text_band': p['_text_band'], '_surround': p['surround'], '_plate_px': plate_px})

    # background refit without the elements
    occupied = np.zeros((H, W), bool)
    for e in elements:
        occupied |= cv2.dilate(e['_foot'].astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    bg_spec, bg_model = fit_background(src, ~occupied & (dist(src, bg_model) < 30))

    order = stacking_order(elements, lambda i, j: above(src, elements[i], elements[j]))
    z = {i: k for k, i in enumerate(order)}
    title_glyph_px = [e['_foot'] & (dist(src, e['_glyph']) < 60) if e['kind'] == 'title' else None for e in elements]

    def occluders(i):
        occ = np.zeros((H, W), bool)
        for j, e in enumerate(elements):
            if j != i and z[j] > z[i]:
                occ |= title_glyph_px[j] if e['kind'] == 'title' else e['_foot']
        return occ

    results, plate_text, pending = {}, {}, []
    from .scene_build import text_frames, _alpha_text
    by_ps = {f.postscript: f for f in fonts}
    for i in [i for i in order if elements[i]['kind'] != 'title'] + [i for i in order if elements[i]['kind'] == 'title']:
        e = elements[i]
        occ = occluders(i)
        if e['kind'] == 'title':
            t = e['line']
            G, ulo, vlo = title_ink(t)
            L, h = G.shape[1], G.shape[0]
            # lettering of plates under this title has the title's colour too: neither ink nor evidence
            below = np.zeros((H, W), bool)
            for j, tm in plate_text.items():
                if z[j] < z[i]:
                    below |= tm
            unk, _ = strip(below.astype(np.uint8), t['cx'], t['cy'], t['theta'], ulo, ulo + L, vlo, vlo + h, nearest=True)
            vis, _ = strip((~occ).astype(np.uint8), t['cx'], t['cy'], t['theta'], ulo, ulo + L, vlo, vlo + h, nearest=True)
            V = (vis > 0) & (unk == 0)
            img, inside = strip(src, t['cx'], t['cy'], t['theta'], ulo, ulo + L, vlo, vlo + h)
            bgl, _ = strip(bg_model, t['cx'], t['cy'], t['theta'], ulo, ulo + L, vlo, vlo + h)
            G = _components_in_band((dist(img, t['glyph']) < np.minimum(dist(img, bgl), 110)) & inside & V, img.shape[0] / 2, t['h'] * .4)
            if _ink_box(G, by_rows=False) is None:
                continue
            per_font = {f.postscript: fit_line(t['text'], G & V, V, [f], _ink_box(G, by_rows=False)) for f in fonts}
            per_font = {k: v for k, v in per_font.items() if v}
            if per_font:
                pending.append((i, t, ulo, vlo, per_font))
            continue
        p = e['plate']
        c = (p['cx'], p['cy'], p['theta'])
        ext = p['h']
        ulo, vlo = -p['w'] / 2 - ext, -p['h'] / 2
        img, inside = strip(src, *c, ulo, -ulo, vlo, -vlo)
        vis, _ = strip((~occ).astype(np.uint8), *c, ulo, -ulo, vlo, -vlo, nearest=True)
        V = (vis > 0) & inside
        in_rect = np.zeros(V.shape, bool)
        in_rect[:, int(ext):V.shape[1] - int(ext)] = True
        G = (dist(img, p['glyph']) < 60) & inside if p['glyph'] is not None else np.zeros(V.shape, bool)
        edge = max(3, int(p['h'] * .06))
        G[:edge] = G[-edge:] = False
        G &= in_rect
        G[:, :int(ext) + edge] = G[:, V.shape[1] - int(ext) - edge:] = False
        G = _components_in_band(G, img.shape[0] / 2, p['text_h'] * .45)
        el = {'kind': e['kind'], 'center': [round(p['cx'], 2), round(p['cy'], 2)], 'size': [round(p['w'], 2), round(p['h'], 2)],
              'rotation_deg': round(-math.degrees(p['theta']), 2), 'fill': p['fill'], 'fade': p['fade'], 'texts': [],
              'shadow': scaled_shadow(e['kind'], p['h'])}
        err = vector_fill_error(src, p, e['_plate_px'] | (p['_foot'] & ~cv2.dilate((dist(src, p['glyph']) < 70).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)) if p['glyph'] is not None else e['_plate_px'])
        el['vector_fill_rmse'] = round(err, 2)
        el['colour_spread'] = round(p.get('spread', 0), 1)
        # textile material (accepted blue ribbons) only for strongly lit bands the vector gradient cannot carry;
        # flat tapes stay flat as in the accepted yellow build
        fade = dict((a, b) for a, b in (p['fade'] or []))
        lit_band = fade.get(28, 0) >= 15 and fade.get(72, 0) >= 15 and fade.get(50, 100) <= 10
        if err > 9 and p['fill']['type'] != 'flat' and lit_band:
            el['material'] = material_fit(src, p, e['_plate_px'])
        glyph_hex = [int(round(v)) for v in p['glyph']] if p['glyph'] is not None else [0, 0, 0]
        if e['repeat']:
            rep = e['repeat']
            best = None
            for l in p['lines']:
                if l['text'].strip() != rep['word']:
                    continue
                u, _ = to_local(*c, l['cx'], l['cy'])
                lo, hi = int(u - l['length'] / 2 - ulo), int(u + l['length'] / 2 - ulo)
                sub = np.zeros_like(G)
                sub[:, max(0, lo):hi] = True
                box = _ink_box(G & sub & V)
                if box is None:
                    continue
                f = fit_line(rep['word'], G & sub & V, V | ~sub, fonts, box)
                if f and (best is None or f['iou'] > best[0]['iou']):
                    best = (f, u)
            if best:
                f, u = best
                bx = f['box']
                off = (bx[0] + bx[2]) / 2 + ulo - u
                el['repeat'] = dict(rep, word=f['text'], font=f['font'].postscript, ink_size=[round(bx[2] - bx[0], 2), round(bx[3] - bx[1], 2)],
                                    ink_v=round(bx[1] + vlo, 2), color=glyph_hex, iou=f['iou'], tracking=f['tracking'], copy_offset=round(off, 2))
                el['repeat']['opacity'] = repeat_opacity(src, p, el['repeat'])
        else:
            span = np.zeros_like(G)
            for l in p['lines']:
                u, _ = to_local(*c, l['cx'], l['cy'])
                lo, hi = int(u - l['length'] / 2 - .3 * l['h'] - ulo), int(u + l['length'] / 2 + .3 * l['h'] - ulo)
                span[:, max(0, lo):max(0, hi)] = True
            G = G & span
            box = _ink_box(G & V)
            if debug_dir is not None:
                from PIL import Image
                Image.fromarray(np.vstack([img, np.dstack([G * 255, V * 255, G * 0]).astype(np.uint8)])).save(
                    '%s/plate-%d.png' % (debug_dir, i))
            if box is not None:
                text = ' '.join(l['text'] for l in sorted(p['lines'], key=lambda l: to_local(*c, l['cx'], l['cy'])[0]))
                f = fit_line(text, G & V, V, fonts, box)
                if f:
                    bx = f['box']
                    el['texts'].append({'text': f['text'], 'ocr_text': text, 'font': f['font'].postscript, 'color': glyph_hex,
                                        'box': [round(bx[0] + ulo, 2), round(bx[1] + vlo, 2), round(bx[2] + ulo, 2), round(bx[3] + vlo, 2)],
                                        'iou': f['iou'], 'tracking': f['tracking'], 'inferred': f['text'] != text or any(f['hidden_ends'])})
        results[i] = el
        el['id'] = 'tmp'
        tm = np.zeros((H, W), np.float32)
        for fr in text_frames(el, fonts):
            tm = np.maximum(tm, _alpha_text(by_ps[fr['font']], fr['text'], fr['m'], fr['tracking'], (H, W)))
        plate_text[i] = cv2.dilate((tm > .2).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    # one typeface per title size class (designs repeat the same face): the font with the best total fit
    for _, t, ulo, vlo, per_font in pending:
        t['_ink_h'] = max(v['box'][3] - v['box'][1] for v in per_font.values())
    for i, t, ulo, vlo, per_font in pending:
        peers = [pf for _, tt, _, _, pf in pending if .6 < tt['_ink_h'] / t['_ink_h'] < 1.6]
        total = {k: sum(pf[k]['iou'] for pf in peers if k in pf) for k in per_font}
        fit = per_font[max(total, key=total.get)]
        m = page_matrix(fit['m'], t['cx'], t['cy'], t['theta'], ulo, vlo)
        bx = fit['box']
        ink_h = bx[3] - bx[1]
        results[i] = ({'kind': 'title', 'text': fit['text'], 'ocr_text': t['text'], 'font': fit['font'].postscript,
                    'color': [int(round(c)) for c in t['glyph']], 'frame': {'cx': round(t['cx'], 2), 'cy': round(t['cy'], 2),
                    'rotation_deg': round(-math.degrees(t['theta']), 2)}, 'box': [round(bx[0] + ulo, 2), round(bx[1] + vlo, 2),
                    round(bx[2] + ulo, 2), round(bx[3] + vlo, 2)], 'm': [round(v, 5) for v in m], 'iou': fit['iou'], 'tracking': fit['tracking'],
                    'inferred': fit['text'] != t['text'] or any(fit['hidden_ends']),
                    'shadow': scaled_shadow('title', ink_h)})
    out = [results[i] for i in order if i in results]
    for k, el in enumerate(out):
        el['id'] = '%02d %s' % (k + 1, el['kind'].upper() + ' ' + (el.get('text') or (el['texts'][0]['text'] if el.get('texts') else
                                                                                          el.get('repeat', {}).get('word', ''))))
    spec = {'schema': SCHEMA, 'size': [W, H], 'unit': 'px = pt', 'background': bg_spec, 'elements': out,
            'presets': {'shadow': SHADOW_PRESETS, 'material_noise': MATERIAL_NOISE}}
    if refine:
        from .scene_refine import refine as _refine
        spec = _refine(spec, src, fonts, log)
    return spec


def scaled_shadow(kind, h):
    return [[round(dx * h, 2), round(dy * h, 2), round(b * h, 2), op] for dx, dy, b, op in SHADOW_PRESETS[kind]]


def repeat_opacity(src, p, rep):
    """Opacity of each copy from the measured lettering contrast (brightest visible copy = 100)."""
    out = {}
    c = (p['cx'], p['cy'], p['theta'])
    for n in range(rep['n'][0], rep['n'][1] + 1):
        u = rep['origin_u'] + n * rep['pitch'] + rep['copy_offset']
        img, inside = strip(src, *c, u - rep['ink_size'][0] / 2, u + rep['ink_size'][0] / 2, rep['ink_v'], rep['ink_v'] + rep['ink_size'][1])
        if inside.mean() < .6:
            continue
        px = img[inside].astype(np.float32)
        g = dist(px, rep['color']) < 70
        if g.sum() < 10:
            continue
        contrast = px[g].mean() - px[~g].mean() if (~g).sum() > 10 else px[g].mean()
        out[n] = float(contrast)
    if not out:
        return {}
    top = max(out.values())
    res = {str(n): int(round(100 * np.clip(v / top, .3, 1))) for n, v in out.items()}
    lo = min(res.values())
    for n in range(rep['n'][0], rep['n'][1] + 1):
        res.setdefault(str(n), lo)
    return res
