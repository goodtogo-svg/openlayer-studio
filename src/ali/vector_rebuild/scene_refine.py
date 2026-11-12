"""Analysis by synthesis for a scene spec (scene.py): render the spec, compare with the source, correct.

The first extraction measures every object on its own. Once geometry, stacking order and shadows are known,
the picture can be re-explained as a whole:
- colours are re-measured only where an object is really seen: not covered by objects above it and not
  darkened by their shadows (a title's shadow on a tape is the title's effect, not the tape's colour);
- a plate whose fill looked graded only because of such shadows becomes a flat fill again;
- ends of plates that disappear under objects above continue under them (amodal completion);
- overlapping titles are stacked in the order whose rendering explains the source better.
"""
import math

import cv2
import numpy as np

from .scene import dist, to_local, to_page


def background_image(bg, W, H):
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    if bg['type'] == 'radial':
        t = np.hypot(xx - bg['origin'][0], yy - bg['origin'][1]) / max(bg['length'], 1)
    else:
        a = math.radians(bg['angle_deg'])
        t = (xx * math.cos(a) + yy * math.sin(a) - bg['start']) / ((bg['end'] - bg['start']) or 1)
    t = np.clip(t, 0, 1) * 100
    offs = [s[0] for s in bg['stops']]
    return np.stack([np.interp(t, offs, [s[1][c] for s in bg['stops']]) for c in range(3)], -1).astype(np.float32)


def plate_fill_image(el, shape):
    from .scene import render_plate_fill
    p = {'cx': el['center'][0], 'cy': el['center'][1], 'theta': -math.radians(el['rotation_deg']), 'w': el['size'][0],
         'h': el['size'][1], 'fill': el['fill'], 'fade': el.get('fade')}
    return render_plate_fill(p, shape)


def layers(spec, fonts, shape):
    """Per element: body alpha, colour image of the body, text alpha (plates) with colour/opacity, shadow alpha."""
    from .scene_build import _alpha_rect, _alpha_text, material_image, shadow_image, text_frames
    by_ps = {f.postscript: f for f in fonts}
    out = []
    for el in spec['elements']:
        frames = text_frames(el, fonts)
        if el['kind'] == 'title':
            fr = frames[0]
            body = _alpha_text(by_ps[fr['font']], fr['text'], fr['m'], fr['tracking'], shape)
            colour = np.broadcast_to(np.array(el['color'], np.float32), shape + (3,))
            texts = []
        else:
            theta = -math.radians(el['rotation_deg'])
            body = _alpha_rect(el['center'][0], el['center'][1], theta, el['size'][0], el['size'][1], shape)
            colour = material_image(el, body).astype(np.float32) if el.get('material') else plate_fill_image(el, shape)
            texts = []
            for fr in frames:
                a = _alpha_text(by_ps[fr['font']], fr['text'], fr['m'], fr['tracking'], shape)
                c = np.array([int(fr['fill'][i:i + 2], 16) for i in (1, 3, 5)], np.float32)
                texts.append((a, c, fr.get('opacity', 1.0)))
        shadow = shadow_image(body, el['shadow']) if el.get('shadow') else np.zeros(shape, np.float32)
        out.append({'body': body, 'colour': colour, 'texts': texts, 'shadow': shadow})
    return out


def composite(spec, L, order=None):
    W, H = spec['size']
    img = background_image(spec['background'], W, H)
    for i in (order if order is not None else range(len(L))):
        l = L[i]
        img = img * (1 - l['shadow'][..., None])
        a = l['body'][..., None]
        img = img * (1 - a) + l['colour'] * a
        for ta, tc, op in l['texts']:
            a = (ta * op)[..., None]
            img = img * (1 - a) + tc * a
    return img


def _above_masks(L):
    """For each element: how much of it is covered by, and how much it is shaded by, elements above it."""
    n = len(L)
    shape = L[0]['body'].shape if n else (1, 1)
    cover, shade = [None] * n, [None] * n
    c = np.zeros(shape, np.float32)
    s = np.zeros(shape, np.float32)
    for i in range(n - 1, -1, -1):
        cover[i], shade[i] = c.copy(), s.copy()
        body = L[i]['body']
        for ta, _, op in L[i]['texts']:
            body = np.maximum(body, ta * op)
        c = 1 - (1 - c) * (1 - body)
        s = 1 - (1 - s) * (1 - L[i]['shadow'])
    return cover, shade


def _core(alpha, px=1):
    m = (alpha > .98).astype(np.uint8)
    return cv2.erode(m, np.ones((2 * px + 1, 2 * px + 1), np.uint8)).astype(bool) if px else m.astype(bool)


def _clean_pixels(src, mask, cover, shade, min_px=40):
    for c_lim, s_lim in ((.02, .03), (.02, .12), (.05, .3)):
        sel = mask & (cover < c_lim) & (shade < s_lim)
        if sel.sum() >= min_px:
            return src[sel].astype(np.float32)
    return None


def _stroke_colour(px, under):
    """Lettering on a plate: thin strokes are softened by blur and print grain toward the plate colour, so the
    ink is the quarter of the lettering cluster farthest from the plate (the stroke cores)."""
    if px is None or len(px) < 10:
        return None
    if len(px) >= 20:
        crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
        _, lab, cen = cv2.kmeans(px, 2, None, crit, 3, cv2.KMEANS_PP_CENTERS)
        lab = lab.ravel()
        k = int(np.argmax([dist(c, under) if (lab == q).sum() >= .15 * len(px) else -1 for q, c in enumerate(cen)]))
        px = px[lab == k]
    d = dist(px, under)
    core = px[d >= np.quantile(d, .75)]
    return np.median(core if len(core) >= 5 else px, 0)


def _surface_colour(px, under):
    """Colour of a painted surface from pixels that should show it: the cluster farthest from what lies under
    it (misfit edges show the underlay), then the most common brightness of that cluster (shadows and grain
    only spread its tails)."""
    if px is None or len(px) < 10:
        return None
    if len(px) >= 20:
        crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
        _, lab, cen = cv2.kmeans(px, 2, None, crit, 3, cv2.KMEANS_PP_CENTERS)
        lab = lab.ravel()
        k = int(np.argmax([dist(c, under) if (lab == q).sum() >= .15 * len(px) else -1 for q, c in enumerate(cen)]))
        px = px[lab == k]
    lum = px.mean(-1)
    hist, edges = np.histogram(lum, bins=64, range=(0, 256))
    hist = np.convolve(hist, np.ones(5) / 5, mode='same')
    mode = (edges[int(np.argmax(hist))] + edges[int(np.argmax(hist)) + 1]) / 2
    near = np.abs(lum - mode) < 10
    return np.median(px[near] if near.sum() >= 5 else px, 0)


def refine_colours(spec, src, fonts, log=print):
    H, W = src.shape[:2]
    L = layers(spec, fonts, (H, W))
    cover, shade = _above_masks(L)
    bgimg = background_image(spec['background'], W, H)
    for i, el in enumerate(spec['elements']):
        l = L[i]
        if el['kind'] == 'title':
            under = np.median(bgimg[l['body'] > .5], 0)
            c = _surface_colour(_clean_pixels(src, _core(l['body']), cover[i], shade[i]), under)
            if c is not None:
                el['color'] = [int(round(v)) for v in c]
            continue
        own_text = np.zeros((H, W), np.float32)
        for ta, _, _ in l['texts']:
            own_text = np.maximum(own_text, ta)
        # lettering colour: the cores of the glyphs that are really visible
        if el.get('texts'):
            under = np.median(l['colour'][own_text > .5], 0) if (own_text > .5).any() else np.zeros(3)
            c = _stroke_colour(_clean_pixels(src, _core(own_text), cover[i], shade[i]), under)
            if c is not None:
                for t in el['texts']:
                    t['color'] = [int(round(v)) for v in c]
        elif el.get('repeat'):
            full = [ta for ta, _, op in l['texts'] if op >= .99]
            if full:
                fa = np.maximum.reduce(full)
                under = np.median(l['colour'][fa > .5], 0)
                c = _stroke_colour(_clean_pixels(src, _core(fa), cover[i], shade[i], min_px=25), under)
                if c is not None:
                    el['repeat']['color'] = [int(round(v)) for v in c]
        if el.get('material'):
            continue
        # plate fill on clean plate pixels: away from lettering, edges, objects above and their shadows
        theta = -math.radians(el['rotation_deg'])
        w, h = el['size']
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        u, v = to_local(el['center'][0], el['center'][1], theta, xx, yy)
        inner = (np.abs(u) < w / 2 - 3) & (np.abs(v) < h / 2 * .86)
        text_free = cv2.dilate((own_text > .05).astype(np.uint8), np.ones((5, 5), np.uint8)) == 0
        clean = inner & text_free & (cover[i] < .02) & (shade[i] < .03) & (src.max(-1) > 0)
        if clean.sum() < 200:
            continue
        px = src[clean].astype(np.float32)
        vb = np.clip(((v[clean] / h + .5) * 5).astype(int), 0, 4)
        prof = [np.median(px[vb == k], 0) if (vb == k).sum() > 30 else None for k in range(5)]
        prof = [p for p in prof if p is not None]
        med = np.median(px, 0)
        spread = max(dist(np.array(p), med) for p in prof) if prof else 0
        if spread < 25:
            el['fill'] = {'type': 'flat', 'color': [int(round(c)) for c in med]}
        # end fade along the length from clean pixels only
        lum = px.mean(-1)
        ub = (u[clean] / w + .5)
        peak = np.percentile(lum, 95)
        fade = []
        for f in (0, .28, .5, .72, 1):
            near = np.abs(ub - f) < .06
            if near.sum() >= 30:
                fade.append([int(f * 100), int(round(float(np.clip(1 - np.median(lum[near]) / peak, 0, .9)) * 100))])
        el['fade'] = fade if len(fade) >= 3 and max(s[1] for s in fade) >= 6 else None
    return spec


def _seg_dist(c, a, b):
    ab = b - a
    t = float(np.clip(np.dot(c - a, ab) / (np.dot(ab, ab) + 1e-6), 0, 1))
    return float(np.linalg.norm(c - (a + t * ab)))


def extend_hidden_ends(spec, src, fonts, log=print):
    """Plate ends that stop at an object above them continue underneath it (as the accepted build did for
    CROSSING): grow along the axis while the extension is hidden by objects above or still shows the plate."""
    H, W = src.shape[:2]
    L = layers(spec, fonts, (H, W))
    cover, _ = _above_masks(L)
    for i, el in enumerate(spec['elements']):
        if el['kind'] == 'title' or el.get('repeat'):
            continue
        theta = -math.radians(el['rotation_deg'])
        w, h = el['size']
        cx, cy = el['center']
        fill = el['fill']
        ref = np.array(fill['color'] if fill['type'] == 'flat' else fill['stops'][2][1], np.float32)
        chroma = ref / (ref.sum() + 1)
        ink = np.array(el['texts'][0]['color'], np.float32) if el.get('texts') else None
        lo, hi = -w / 2, w / 2
        for side in (-1, 1):
            end = side * w / 2
            pos, last_like, step = 0.0, 0.0, 2.0
            while pos < 1.2 * w:
                pos += step
                u = end + side * pos
                like = hid = lettering = n = 0
                for v in np.linspace(-h / 2 * .8, h / 2 * .8, 9):
                    x, y = to_page(cx, cy, theta, u, v)
                    xi, yi = int(round(x)), int(round(y))
                    if not (0 <= xi < W and 0 <= yi < H):
                        continue
                    n += 1
                    c = src[yi, xi].astype(np.float32)
                    if np.abs(c / (c.sum() + 1) - chroma).sum() < .16 and .3 < c.sum() / (ref.sum() + 1) < 1.5:
                        like += 1
                    elif ink is not None and (dist(c, ink) < 60 or _seg_dist(c, ink, ref) < 35 or _seg_dist(c, ink, ref * .55) < 35):
                        lettering += 1   # lettering and its anti-aliased edge on the plate
                    elif cover[i][yi, xi] > .5:
                        hid += 1
                if n < 5:
                    break
                if like >= 1 and like + lettering + hid >= .6 * n:
                    last_like = pos          # the plate is still seen here (lit, shaded, lettered or partly covered)
                elif hid + like + lettering >= .6 * n and pos - last_like < 1.5 * h:
                    continue                 # hidden under an object above: look for the plate on its far side
                else:
                    break
            if last_like >= 4:
                if side < 0:
                    lo -= last_like
                else:
                    hi += last_like
        if hi - lo > w + 3:
            nu = (lo + hi) / 2
            el['center'] = [round(v, 2) for v in to_page(cx, cy, theta, nu, 0)]
            el['size'] = [round(hi - lo, 2), h]
            for t in el.get('texts', []):
                t['box'] = [round(t['box'][0] - nu, 2), t['box'][1], round(t['box'][2] - nu, 2), t['box'][3]]
            log('refine: %s extended under objects above to %.0f' % (el['id'], hi - lo))
    return spec


def restack_titles(spec, src, fonts, log=print):
    """Overlapping titles: keep the order whose rendering (glyphs + their shadows) matches the source better."""
    H, W = src.shape[:2]
    els = spec['elements']
    L = layers(spec, fonts, (H, W))
    srcf = src.astype(np.float32)
    order = list(range(len(els)))
    changed = False
    for i in range(len(els)):
        for j in range(i + 1, len(els)):
            if els[i]['kind'] != 'title' or els[j]['kind'] != 'title':
                continue
            ov = (L[i]['body'] > .3) & (cv2.dilate((L[j]['body'] > .3).astype(np.uint8), np.ones((31, 31), np.uint8)) > 0)
            if ov.sum() < 30:
                continue
            region = cv2.dilate(((L[i]['body'] > .05) | (L[j]['body'] > .05)).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
            region &= (L[i]['shadow'] > .02) | (L[j]['shadow'] > .02) | (L[i]['body'] > .05) | (L[j]['body'] > .05)
            pi = order.index(i)
            alt = list(order)
            alt.remove(j)
            alt.insert(pi, j)  # j directly below i
            e0 = np.abs(composite(spec, L, order) - srcf)[region].mean()
            e1 = np.abs(composite(spec, L, alt) - srcf)[region].mean()
            if e1 < e0 * .98:
                order = alt
                changed = True
                log('refine: %s now below %s (error %.2f -> %.2f)' % (els[j]['id'], els[i]['id'], e0, e1))
    if changed:
        spec['elements'] = [els[k] for k in order]
        for k, el in enumerate(spec['elements']):
            el['id'] = '%02d %s' % (k + 1, el['id'].split(' ', 1)[1])
    return spec


def complete_title_proportions(spec, src, fonts, log=print):
    """A title whose top or bottom runs under an object above was fitted to its visible ink only and comes out
    squashed. Lines of one size class share the face's vertical scale: restore it, keeping the visible edge."""
    from .scene_build import _alpha_text
    H, W = src.shape[:2]
    by_ps = {f.postscript: f for f in fonts}
    els = spec['elements']
    L = layers(spec, fonts, (H, W))
    cover, _ = _above_masks(L)
    titles = [i for i, e in enumerate(els) if e['kind'] == 'title' and abs(e['m'][1]) < 1e-6 and abs(e['m'][2]) < 1e-6]
    for i in titles:
        e = els[i]
        peers = [els[j]['m'][3] for j in titles if j != i and els[j]['font'] == e['font'] and .6 < els[j]['m'][3] / e['m'][3] < 1.7]
        if len(peers) < 2:
            continue
        target = float(np.median(peers))
        if e['m'][3] > .9 * target:
            continue
        ys, xs = np.nonzero(L[i]['body'] > .5)
        if len(ys) == 0:
            continue
        top, bot, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
        band = lambda y0, y1: cover[i][max(0, y0):min(H, y1), x0:x1 + 1]
        hid_bot = band(bot + 1, bot + 6).mean() if bot + 1 < H else 0
        hid_top = band(top - 6, top).mean() if top > 0 else 0
        if max(hid_bot, hid_top) < .3:
            continue
        m = list(e['m'])
        m[3] = target
        a = _alpha_text(by_ps[e['font']], e['text'], tuple(m), e.get('tracking', 0), (H, W))
        ys2, _ = np.nonzero(a > .5)
        if len(ys2) == 0:
            continue
        shift = (top - ys2.min()) if hid_bot >= hid_top else (bot - ys2.max())
        m[5] += float(shift)
        e['m'] = [round(v, 5) for v in m]
        e['box'] = [e['box'][0], e['box'][1], e['box'][2], e['box'][3]]
        e['inferred'] = True
        log('refine: %s vertical scale %.3f -> %.3f (hidden %s)' % (e['id'], els[i]['m'][3] if False else e['m'][3], target,
                                                                      'bottom' if hid_bot >= hid_top else 'top'))
    return spec


PUNCTUATION = (' \u2013', '\u2013', ' -', '-', ' \u2014', ':', '.', '!', '?')


def complete_punctuation(spec, src, fonts, log=print):
    """OCR often drops trailing punctuation (THIS MEANS -). Ink of the title's colour that no element explains,
    on the title's line just after its end, is tried as punctuation set in the same face and size."""
    from .scene_build import _alpha_text
    H, W = src.shape[:2]
    by_ps = {f.postscript: f for f in fonts}
    L = layers(spec, fonts, (H, W))
    img = composite(spec, L)
    srcf = src.astype(np.float32)
    for i, e in enumerate(spec['elements']):
        if e['kind'] != 'title' or not by_ps.get(e['font']):
            continue
        body = L[i]['body']
        ys, xs = np.nonzero(body > .5)
        if len(xs) == 0:
            continue
        x1, top, bot = xs.max(), ys.min(), ys.max()
        h = bot - top + 1
        col = np.array(e['color'], np.float32)
        unexplained = (dist(srcf, img) > 30) & (dist(srcf, col) < dist(srcf, img))   # nearer the title's ink than the rendering
        win = np.zeros((H, W), bool)
        win[top:bot + 1, x1 + 1:min(W, x1 + 1 + int(1.5 * h))] = True
        res = unexplained & win
        if res.sum() < 12:
            continue
        best = None
        for p in PUNCTUATION:
            font = by_ps[e['font']]
            if not font.covers(p.strip()):
                continue
            a = _alpha_text(font, e['text'] + p, tuple(e['m']), e.get('tracking', 0), (H, W))
            extra = (a > .5) & ~(body > .5)
            u = (extra | res).sum()
            iou = (extra & res).sum() / u if u else 0
            if best is None or iou > best[0]:
                best = (iou, p)
        if best and best[0] > .35:
            e['text'] = e['text'] + best[1]
            e['inferred'] = True
            log('refine: %s + %r (unexplained ink IoU %.2f)' % (e['id'], best[1], best[0]))
    return spec


def refine(spec, src, fonts, log=print):
    spec = extend_hidden_ends(spec, src, fonts, log)
    spec = complete_title_proportions(spec, src, fonts, log)
    spec = restack_titles(spec, src, fonts, log)
    spec = refine_colours(spec, src, fonts, log)
    spec = complete_punctuation(spec, src, fonts, log)
    return spec
