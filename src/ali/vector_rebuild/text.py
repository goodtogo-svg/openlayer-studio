"""Text analysis: OCR lines -> fitted live-text specs (ink box, skew, colours, decorations).

The fitting rule is the one used by an earlier user-accepted Illustrator build:
lay the string out at a nominal size with a skew, then scale its ink box non-uniformly onto the
measured ink box of the glyph pixels and align the ink top-left corner.
"""
import math
import re

import cv2
import numpy as np
from fontTools.pens.basePen import BasePen

NOMINAL = 120.0
CJK = re.compile(r'[㐀-鿿豈-﫿]')


class CairoPen(BasePen):
    def __init__(self, ctx):
        super().__init__(None)
        self.ctx = ctx

    def _moveTo(self, p):
        self.ctx.move_to(*p)

    def _lineTo(self, p):
        self.ctx.line_to(*p)

    def _curveToOne(self, p1, p2, p3):
        self.ctx.curve_to(*p1, *p2, *p3)

    def _closePath(self):
        self.ctx.close_path()


def rasterise(font, text, size, matrix, shape, offset=(0, 0), tracking=0.0):
    import cairocffi as cairo
    h, w = shape
    surface = cairo.ImageSurface(cairo.FORMAT_A8, w, h)
    ctx = cairo.Context(surface)
    ctx.translate(-offset[0], -offset[1])
    font.draw(text, size, matrix, CairoPen(ctx), tracking)
    ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
    ctx.fill()
    surface.flush()
    stride = surface.get_stride()
    data = np.frombuffer(surface.get_data(), np.uint8).reshape(h, stride)[:, :w]
    return data > 127


def fit_matrix(font, text, box, skew, tracking=0.0):
    """Matrix that puts the ink of text (laid out at NOMINAL with skew) exactly on box=(x,y,w,h)."""
    x, y, w, h = box
    x0, y0, x1, y1 = font.ink(text, NOMINAL, (1, 0, -skew, 1, 0, 0), tracking)
    sx, sy = w / (x1 - x0), h / (y1 - y0)
    m = (sx, 0, -skew * sx, sy, 0, 0)
    x0, y0, _, _ = font.ink(text, NOMINAL, m, tracking)
    return (sx, 0, -skew * sx, sy, x - x0, y - y0)


def normalise_text(text, locale='zh-TW'):
    text = text.strip()
    # A Latin x between CJK characters is almost always a drawn multiplication sign.
    text = re.sub(r'(?<=[㐀-鿿])\s*[xX×]\s*(?=[㐀-鿿]|$)', ' × ', text)
    if locale.lower() in ('zh-tw', 'zh-hant'):
        try:
            import opencc
            text = opencc.OpenCC('s2twp').convert(text)
        except ImportError:
            pass
    return text.strip()


def merge_lines(records, min_conf=0.5):
    """Join OCR boxes that sit on one line (same height band, small gap)."""
    items = []
    for r in records:
        poly = np.array(r['polygon'], float)
        text = r['text'].strip()
        if r['confidence'] < min_conf or not text:
            continue
        if len(text) < 2 and not CJK.search(text):
            continue
        x0, y0 = poly.min(0)
        x1, y1 = poly.max(0)
        tilt = abs(math.degrees(math.atan2(poly[1][1] - poly[0][1], poly[1][0] - poly[0][0])))
        items.append({'text': text, 'conf': r['confidence'], 'box': [x0, y0, x1, y1], 'polys': [poly], 'tilted': tilt > 1.0})
    items.sort(key=lambda i: i['box'][0])
    lines = []
    for it in items:
        for ln in ([] if it['tilted'] else lines):
            if ln['tilted']:
                continue
            a, b = ln['box'], it['box']
            h = min(a[3] - a[1], b[3] - b[1])
            overlap = min(a[3], b[3]) - max(a[1], b[1])
            gap = b[0] - a[2]
            if overlap > 0.6 * h and -0.3 * h < gap < 0.8 * h and abs((a[3] - a[1]) - (b[3] - b[1])) < 0.35 * h:
                ln['text'] += ' ' + it['text'] if gap > 0.25 * h else it['text']
                ln['box'] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                ln['polys'] += it['polys']
                ln['conf'] = min(ln['conf'], it['conf'])
                break
        else:
            lines.append(dict(it))
    return lines


def _char_colours(src, region, box, text):
    """Per-character ink colour: k-means per cell, cluster farthest from the surrounding background."""
    x0, y0, x1, y1 = [int(v) for v in box]
    ring = cv2.dilate(region, np.ones((15, 15), np.uint8)) - region
    bg = np.median(src[ring > 0], axis=0)
    chars = [c for c in text]
    widths = np.array([1.0 if CJK.match(c) else (0.35 if c == ' ' else 0.6) for c in chars])
    edges = x0 + np.concatenate([[0], np.cumsum(widths)]) / widths.sum() * (x1 - x0)
    colours = []
    for i, ch in enumerate(chars):
        if ch == ' ':
            colours.append(None)
            continue
        cx0, cx1 = int(edges[i]), max(int(edges[i]) + 2, int(edges[i + 1]))
        cell = src[y0:y1, cx0:cx1][region[y0:y1, cx0:cx1] > 0].astype(np.float32)
        if len(cell) < 9:
            colours.append(None)
            continue
        _, labels, centres = cv2.kmeans(cell, 3, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1), 3, cv2.KMEANS_PP_CENTERS)
        counts = np.bincount(labels.ravel(), minlength=3) / len(labels)
        best = max((c for c in range(3) if counts[c] > 0.08), key=lambda c: np.linalg.norm(centres[c] - bg))
        colours.append(centres[best])
    groups = []
    for c in colours:
        if c is None:
            continue
        g = next((g for g in groups if np.linalg.norm(g[0] - c) < 100), None)   # shading inside one ink is not a run
        if g is None:
            groups.append([c])
        else:
            g.append(c)
    snapped = [np.mean(g, axis=0) for g in groups]
    raw = [c for c in colours if c is not None]
    out = []
    for c in colours:
        out.append(None if c is None else min(snapped, key=lambda s: np.linalg.norm(s - c)))
    # A cell that caught mostly background (loose OCR box edges, busy surroundings) picks the box's dominant
    # colour; when that colour is a minority among the characters, give those cells the majority ink.
    px = src[y0:y1, x0:x1][region[y0:y1, x0:x1] > 0].astype(np.float32)
    if len(snapped) > 1 and len(px) > 30:
        _, lab, cen = cv2.kmeans(px, 3, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1), 3, cv2.KMEANS_PP_CENTERS)
        box_bg = cen[int(np.bincount(lab.ravel(), minlength=3).argmax())]
        idx = [None if c is None else min(range(len(snapped)), key=lambda k: np.linalg.norm(snapped[k] - c)) for c in out]
        counts = np.bincount([i for i in idx if i is not None], minlength=len(snapped))
        major = int(counts.argmax())
        for k in range(len(snapped)):
            if k != major and counts[k] < counts[major] and np.linalg.norm(snapped[k] - box_bg) < 80:
                out = [snapped[major] if i == k else c for i, c in zip(idx, out)]
    # spaces take the colour of the previous character
    for i in range(len(out)):
        if out[i] is None:
            out[i] = out[i - 1] if i and out[i - 1] is not None else next((c for c in out if c is not None), bg)
    return out, bg, raw


def _glyph_mask(src, region, colours, line_h):
    d = np.full(region.shape, 1e9, np.float32)
    for c in {tuple(c) for c in colours}:
        d = np.minimum(d, np.linalg.norm(src.astype(np.float32) - np.array(c, np.float32), axis=2))
    m = ((d < 70) & (region > 0)).astype(np.uint8) * 255
    count, labels, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    keep = np.zeros(count, bool)
    for c in range(1, count):
        x, y, w, h, a = stats[c]
        # joined glyphs make wide components; thin wide art (swooshes, rules) has little fill
        keep[c] = a >= 12 and h <= 1.3 * line_h and (w <= 2.0 * line_h or a > 0.18 * w * h)
    return keep[labels]


def _occluder(img, box, colours, bg, line_h, glyph):
    """Pixels inside the line's box that are neither glyph ink nor the line's background and that belong to
    something extending beyond the box (a tape, a banner) - the text is probably behind it there."""
    H, W = img.shape[:2]
    pad = int(line_h * 0.3)
    x0, y0 = max(0, int(box[0]) - pad), max(0, int(box[1]) - pad)
    x1, y1 = min(W, int(box[2]) + pad), min(H, int(box[3]) + pad)
    crop = img[y0:y1, x0:x1].astype(np.float32)
    d_ink = np.min([np.linalg.norm(crop - np.array(c, np.float32), axis=2) for c in colours], axis=0)
    d_bg = np.linalg.norm(crop - np.array(bg, np.float32), axis=2)
    cand = ((d_ink > 70) & (d_bg > 60)).astype(np.uint8)
    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    # outlines and shadows hug the glyphs: only colour that also exists away from the glyphs can be in front
    reach = max(3, int(line_h * 0.15))
    far_from_ink = cv2.distanceTransform(1 - glyph[y0:y1, x0:x1].astype(np.uint8), cv2.DIST_L2, 3) > reach
    full_cand = cand
    cand = cand & far_from_ink.astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(cand, 8)
    out = np.zeros((H, W), bool)
    h, w = cand.shape
    for c in range(1, count):
        x, y, cw, ch, a = stats[c]
        touches = x == 0 or y == 0 or x + cw == w or y + ch == h
        if touches and a > 0.02 * h * w and cw > 0.5 * w:
            out[y0:y1, x0:x1] |= labels == c
    if out.any():
        grown = cv2.dilate(out[y0:y1, x0:x1].astype(np.uint8), np.ones((2 * reach + 1,) * 2, np.uint8)) > 0
        out[y0:y1, x0:x1] = grown & (full_cand > 0)
    return out


def _hex(c):
    return '#%02x%02x%02x' % tuple(int(round(v)) for v in c)


def _decorations(src, glyph, fill_colours, bg):
    """Outline (stroke behind the glyphs) and drop shadow, measured around the glyph mask."""
    g = glyph.astype(np.uint8)
    dist = cv2.distanceTransform(1 - g, cv2.DIST_L2, 5)
    near = (dist > 1.5) & (dist < 4)
    if near.sum() < 50:
        return []
    ring = src[near].astype(np.float32)
    outline = np.median(ring, axis=0)
    fills = [np.array(c, np.float32) for c in fill_colours]
    if np.linalg.norm(outline - bg) < 60 or min(np.linalg.norm(outline - f) for f in fills) < 40:
        return []
    close = (np.linalg.norm(src.astype(np.float32) - outline, axis=2) < 45) & (dist > 0) & (dist < 40)
    count, labels = cv2.connectedComponents((close | glyph).astype(np.uint8), 8)
    attached = np.unique(labels[glyph])
    plate = np.isin(labels, attached[attached > 0]) & ~glyph
    if plate.sum() < 50:
        return []
    # A real outline has a crisp outer edge; a soft (blurred) drop shadow fades into the background.
    pl = plate.astype(np.uint8)
    edge = plate & (cv2.erode(pl, np.ones((3, 3), np.uint8)) == 0)
    outside = (cv2.dilate(pl, np.ones((7, 7), np.uint8)) > 0) & ~plate & ~glyph
    if edge.sum() and outside.sum():
        contrast = np.linalg.norm(np.median(src[edge].astype(np.float32), 0) - np.median(src[outside].astype(np.float32), 0))
        if contrast < 35 or float(src[plate].astype(np.float32).std(0).mean()) > 22:
            return []
    width = float(np.percentile(dist[plate], 90))
    ys, xs = np.nonzero(glyph)
    py, px = np.nonzero(plate)
    down = py.max() - ys.max()
    up = ys.min() - py.min()
    # an outline surrounds the glyphs on every side and is thin compared with them
    if up < 1 or width > 0.15 * (ys.max() - ys.min() + 1):
        return []
    decos = [{'name': 'OUTLINE', 'color': _hex(outline), 'stroke': round(2 * min(width, up + 0.5), 2), 'dy': 0}]
    if down - up > 2:
        decos.insert(0, {'name': 'SHADOW', 'color': _hex(outline * 0.92 + np.array([0, 4, 8])), 'stroke': round(2 * min(width, up + 0.5) + 1, 2), 'dy': round(float(down - up), 2)})
    return decos


def _ink_box(glyph):
    """Ink box that ignores thin strokes (swooshes, hairlines) and stray sparks: thin-open, then mass percentiles."""
    dist = cv2.distanceTransform(glyph.astype(np.uint8), cv2.DIST_L2, 3)
    stroke = 2 * float(np.median(dist[dist > 0])) if (dist > 0).any() else 2
    k = max(3, int(stroke * 0.6) | 1)
    core = cv2.morphologyEx(glyph.astype(np.uint8), cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    if core.sum() < 0.3 * glyph.sum():
        core = glyph.astype(np.uint8)
    cols, rows = core.sum(0).astype(float), core.sum(1).astype(float)
    def span(v, lo=0.005, hi=0.995):
        c = np.cumsum(v) / v.sum()
        return float(np.searchsorted(c, lo)), float(np.searchsorted(c, hi) + 1)
    x0, x1 = span(cols)
    y0, y1 = span(rows)
    # grow back to the true ink edge of the kept core (opening shaved the outline)
    ys, xs = np.nonzero(glyph[int(y0):int(y1), int(x0):int(x1)])
    return x0 + xs.min(), y0 + ys.min(), x0 + xs.max() + 1, y0 + ys.max() + 1


def _refine(best, text, observed, keep_px, offset, occluded):
    """Same-coloured art fused to the glyphs (swooshes, underlines) widens the ink box, and art in front of the
    text shortens it: try trimming or growing each side."""
    bx, by, bw, bh = best['box']
    grow_w, grow_h = ((1.05, 1.1), (1.1, 1.2, 1.3, 1.45)) if occluded else ((), ())
    for fw in (1.0, .97, .94, .91, .88, .85) + grow_w:
        for fh in (1.0, .95, .9) + grow_h:
            if fw == 1.0 and fh == 1.0:
                continue
            for ax in ((0, 1) if fw != 1 else (0,)):
                for ay in ((0, 1) if fh != 1 else (0,)):
                    cand = (bx + ax * bw * (1 - fw), by + ay * bh * (1 - fh), bw * fw, bh * fh)
                    m = fit_matrix(best['font'], text, cand, best['skew'], best['tracking'])
                    rendered = rasterise(best['font'], text, NOMINAL, m, observed.shape, offset, best['tracking']) & keep_px
                    iou = (rendered & observed).sum() / max(1, (rendered | observed).sum())
                    if iou > best['iou'] + 0.005:
                        best = dict(best, iou=float(iou), m=m, box=cand)
    return best


def _fit(text, glyph, fonts, line_h, skews, trackings, ignore=None):
    """Fit text to glyph pixels; prunes glyph-coloured art the fit does not explain. Returns (best, glyph).
    ignore: pixels covered by something in front of the text (they neither count for nor against a fit)."""
    H, W = glyph.shape
    if ignore is None:
        ignore = np.zeros((H, W), bool)
    best, overall = None, None
    for _ in range(3):
        if glyph.sum() < 50:
            break
        bx0, by0, bx1, by1 = _ink_box(glyph)
        box = (float(bx0), float(by0), float(bx1 - bx0), float(by1 - by0))
        pad = int(line_h * 0.3)
        ox, oy = max(0, int(bx0) - pad), max(0, int(by0) - pad)
        ex, ey = min(W, int(bx1) + pad), min(H, int(by1) + pad)
        observed = glyph[oy:ey, ox:ex]
        keep_px = ~ignore[oy:ey, ox:ex]
        occluded = not keep_px.all()
        coarse = []
        for font in fonts:
            if not font.covers(text.replace(' ', '')):
                continue
            for skew in skews:
                for track in (trackings if len(text) > 1 else [0.0]):
                    m = fit_matrix(font, text, box, skew, track)
                    rendered = rasterise(font, text, NOMINAL, m, observed.shape, (ox, oy), track) & keep_px
                    iou = (rendered & observed).sum() / max(1, (rendered | observed).sum())
                    coarse.append({'iou': float(iou), 'font': font, 'skew': float(skew), 'm': m, 'box': box, 'tracking': float(track)})
        if not coarse:
            break
        # Refine the box for the strongest few candidates; upright text wins unless a slant clearly fits better.
        coarse.sort(key=lambda c: -c['iou'])
        shortlist, seen = [], set()
        for c in coarse:
            per_font = sum(1 for x in shortlist if x['font'] is c['font'])
            if per_font < 3:
                shortlist.append(c)
        upright = [c for c in coarse if c['skew'] == min(skews)]
        if upright and upright[0] not in shortlist:
            shortlist.append(upright[0])
        best = max((_refine(c, text, observed, keep_px, (ox, oy), occluded) for c in shortlist),
                   key=lambda c: c['iou'] - (0.3 if occluded else 0.1) * c['skew'])   # a slanted cut-off edge is not italics
        # pruning can also cut real letters when the fit is off; keep the best round, not the last
        if overall is None or best['iou'] > overall[0]['iou']:
            overall = (best, glyph)
        full = np.zeros((H, W), bool)
        full[oy:ey, ox:ex] = rasterise(best['font'], text, NOMINAL, best['m'], observed.shape, (ox, oy), best['tracking'])
        near = cv2.dilate(full.astype(np.uint8), np.ones((int(line_h * 0.06) | 1,) * 2, np.uint8)) > 0
        count, labels = cv2.connectedComponents(glyph.astype(np.uint8), 8)
        areas = np.bincount(labels.ravel(), minlength=count)
        inside = np.bincount(labels[near].ravel(), minlength=count)
        keep = inside >= 0.6 * areas
        keep[0] = False
        pruned = keep[labels]
        # only a fit that already explains most of the ink may decide what is not lettering
        if best['iou'] < 0.45 or pruned.sum() == glyph.sum() or pruned.sum() < 50:
            break
        glyph = pruned
    return overall if overall else (None, glyph)


def _runs(text, colours):
    out, i = [], 0
    while i < len(text):
        j = i
        while j < len(text) and np.array_equal(colours[j], colours[i]):
            j += 1
        out.append((i, j, colours[i]))
        i = j
    return out


def _entry(name, text, best, fill, runs, ocr):
    x, y, w, h = best['box']
    return {'name': name, 'text': text, 'font': best['font'].postscript, 'x': x, 'y': y, 'w': w, 'h': h,
            'skew': round(best['skew'], 3), 'tracking': round(best['tracking'], 3), 'fill': _hex(fill),
            'runs': [{'start': a, 'end': b, 'fill': _hex(c)} for a, b, c in runs if not np.array_equal(c, fill)],
            'score': round(best['iou'], 3), 'ocr': ocr}


def analyse_text(src, records, fonts, locale='zh-TW', min_score=0.45, skews=None, trackings=None, debug_dir=None):
    """Returns (specs, glyph_mask_union, report). Coordinates are source pixels."""
    H, W = src.shape[:2]
    # Negative tracking makes glyphs overlap, and Illustrator then breaks the line into fragments on PDF import;
    # tight setting is matched by the horizontal scale instead.
    trackings = trackings if trackings is not None else [-0.06, -0.03, 0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3]
    skews = skews if skews is not None else [round(s, 2) for s in np.arange(0, 0.36, 0.02)]
    specs, report = [], []
    union = np.zeros((H, W), bool)
    for n, line in enumerate(merge_lines(records)):
        text = normalise_text(line['text'], locale)
        # Rotated lines (tapes, stickers): fit in a frame where the line is horizontal.
        angle = float(np.median([math.degrees(math.atan2(p[1][1] - p[0][1], p[1][0] - p[0][0])) for p in line['polys']]))
        img, R_inv = src, None
        if abs(angle) > 1.0:
            centre = tuple(np.concatenate(line['polys']).mean(0))
            R = cv2.getRotationMatrix2D(centre, angle, 1.0)
            R_inv = cv2.invertAffineTransform(R)
            img = cv2.warpAffine(src, R, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            polys = [cv2.transform(p[None].astype(np.float32), R)[0] for p in line['polys']]
            line = dict(line, polys=polys)
            allp = np.concatenate(polys)
            line['box'] = [allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max()]
        region = np.zeros((H, W), np.uint8)
        for poly in line['polys']:
            cv2.fillPoly(region, [np.round(poly).astype(np.int32)], 255)
        region = cv2.dilate(region, np.ones((5, 5), np.uint8))
        line_h = line['box'][3] - line['box'][1]
        colours, bg, raw = _char_colours(img, region, line['box'], text)
        # Keep colour runs only when they look designed: every run at least two letters, every ink well away
        # from the background. Otherwise shading or a stray cell would split one word into several objects.
        line_runs = [(a, b_, c) for a, b_, c in _runs(text, colours) if text[a:b_].strip()]
        designed = all((len(text[a:b_].strip()) >= 2 or not text[a:b_].strip().isalnum())
                       and np.linalg.norm(np.array(c, float) - bg) > 100 for a, b_, c in line_runs) and \
            all(np.linalg.norm(np.array(p[2], float) - np.array(q[2], float)) > 100
                for i, p in enumerate(line_runs) for q in line_runs[i + 1:] if not np.array_equal(p[2], q[2]))
        if len(line_runs) > 1 and not designed:
            lens = {}
            for a, b_, c in line_runs:
                lens[tuple(np.round(c, 3))] = lens.get(tuple(np.round(c, 3)), 0) + len(text[a:b_].strip())
            major = np.array(max(lens, key=lens.get), np.float32)
            colours = [major for _ in colours]
        glyph = _glyph_mask(img, region, colours, line_h)
        occ = _occluder(img, line['box'], colours, bg, line_h, glyph)
        if glyph.sum() < 50:
            report.append({'text': text, 'decision': 'no_glyph_pixels'})
            continue
        best, g = _fit(text, glyph, fonts, line_h, skews, trackings, occ)
        if best is None:
            report.append({'text': text, 'decision': 'no_font_covers_text'})
            continue
        runs = _runs(text, colours)
        candidates = [[(text, best, g, colours[0], runs)]]
        # A designer often sets colour runs as separate frames with their own spacing: try that too.
        solid = [(a, b, c) for a, b, c in runs if text[a:b].strip()]
        distinct = all(np.linalg.norm(np.array(p[2], float) - np.array(q[2], float)) > 80
                       for i, p in enumerate(solid) for q in solid[i + 1:])
        if len(solid) > 1 and best['iou'] < 0.7 and distinct and all(len(text[a:b].strip()) >= 2 for a, b, _ in solid):
            parts = []
            for a, b, c in solid:
                part = text[a:b].strip()
                d = np.linalg.norm(img.astype(np.float32) - np.array(c, np.float32), axis=2)
                pg = glyph & (d < 70)
                pb, pgl = _fit(part, pg, fonts, line_h, skews, trackings, occ)
                if pb is None:
                    parts = None
                    break
                parts.append((part, pb, pgl, c, [(0, len(part), c)]))
            if parts:
                # Parts of one line share its height band and slant: refit weak parts inside the best part's band.
                ref = max(parts, key=lambda p: p[1]['iou'])[1]
                rx, ry, rw, rh = ref['box']
                for k, (part, pb, pgl, c, pr) in enumerate(parts):
                    if pb is ref or pb['iou'] >= ref['iou'] - 0.1:
                        continue
                    band = np.zeros_like(pgl)
                    band[int(max(0, ry - 0.04 * rh)):int(min(H, ry + rh * 1.04)), :] = True
                    d = np.linalg.norm(img.astype(np.float32) - np.array(c, np.float32), axis=2)
                    nb, ng = _fit(part, glyph & (d < 70) & band, fonts, line_h, [ref['skew']], trackings, occ)
                    if nb is not None and nb['iou'] > pb['iou']:
                        parts[k] = (part, nb, ng, c, pr)
                candidates.append(parts)
        def quality(cand):
            area = [p[2].sum() for p in cand]
            return sum(p[1]['iou'] * a for p, a in zip(cand, area)) / max(1, sum(area))
        chosen = max(candidates, key=quality)
        if len(chosen) > 1 and quality(chosen) < quality(candidates[0]) + 0.05:
            chosen = candidates[0]
        for k, (part, pbest, pglyph, fill, pruns) in enumerate(chosen):
            name = 'TEXT_%02d' % n + ('' if len(chosen) == 1 else chr(97 + k))
            entry = _entry(name, part, pbest, fill, pruns, line['text'])
            if debug_dir:
                from PIL import Image
                ren = rasterise(pbest['font'], part, NOMINAL, pbest['m'], (H, W), (0, 0), pbest['tracking'])
                v = np.zeros((H, W, 3), np.uint8)
                v[..., 0] = pglyph * 255
                v[..., 1] = ren * 255
                Image.fromarray(v).save('%s/fit-%s.png' % (debug_dir, name))
            # Confident OCR of CJK lettering in an unknown display face: accept a looser outline match.
            limit = min_score if not (line['conf'] >= 0.85 and CJK.search(part)) else min(min_score, 0.33)
            if pbest['iou'] < limit:
                report.append(dict(entry, decision='kept_as_artwork_poor_fit'))
                continue
            entry['decorations'] = _decorations(img, pglyph, [fill], bg)
            ren = rasterise(pbest['font'], part, NOMINAL, pbest['m'], (H, W), (0, 0), pbest['tracking'])
            if ren.sum() and (ren & occ).sum() > 0.05 * ren.sum():
                entry['behind'] = True
            if R_inv is not None:
                entry['frame'] = [list(map(float, R_inv[0])), list(map(float, R_inv[1]))]
                entry['angle'] = round(angle, 2)
                pglyph = cv2.warpAffine(pglyph.astype(np.uint8), R_inv, (W, H), flags=cv2.INTER_NEAREST) > 0
            specs.append(entry)
            report.append(dict(entry, decision='live_text'))
            union |= pglyph
    return specs, union, report


def page_matrix(spec, m):
    """Compose a fitted text matrix with the spec's rotation frame (if the line was fitted rotated)."""
    if not spec.get('frame'):
        return m
    (p, q, r), (t, u, v) = spec['frame']
    a, b, c, d, e, f = m
    return (p * a + q * b, t * a + u * b, p * c + q * d, t * c + u * d, p * e + q * f + r, t * e + u * f + v)
