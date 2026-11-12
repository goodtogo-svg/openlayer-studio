"""Image -> analysed vector rebuild (SVG with named groups + live text, vector PDF / PDF-compatible .ai).

Generic: everything is measured from the image, the OCR records and a segmentation partition.
"""
import json
import math
from pathlib import Path

import cv2
import numpy as np

from .arcs import arc_text, badge_candidates
from .fonts import load_candidates
from .output import text_layer, write_pdf
from .shapes import fit_geometry, fit_paint, shape_element, trace_region
from .text import NOMINAL, analyse_text, fit_matrix, page_matrix, rasterise


def _hex(c):
    return '#%02x%02x%02x' % tuple(int(np.clip(round(v), 0, 255)) for v in c)


def background_gradient(src, bg_mask, stops=12):
    """Multi-stop gradient from medians of background pixels (the accepted 'palette band' method), along x, along y
    or radially from the page centre - whichever reproduces the background pixels best."""
    H, W = bg_mask.shape
    yy, xx = np.mgrid[0:H, 0:W]
    rr = np.hypot(xx - W / 2, yy - H / 2)
    axes = {'x': (xx.astype(np.float32), W - 1), 'y': (yy.astype(np.float32), H - 1), 'r': (rr.astype(np.float32), float(rr.max()))}
    flat = np.median(src[bg_mask] if bg_mask.any() else src.reshape(-1, 3), axis=0)
    best = None
    for name, (coord, span) in axes.items():
        pos = np.linspace(0, span, stops)
        half = max(2.0, span / (stops * 2))
        out = []
        for p in pos:
            sel = bg_mask & (np.abs(coord - p) <= half)
            px = src[sel]
            if len(px) >= 20:
                out.append((p / span, np.median(px, axis=0)))
        if len(out) < 2:
            out = [(0, flat), (1, flat)]
        offs = np.array([o for o, _ in out]) * span
        cols = np.array([c for _, c in out], np.float32)
        model = np.stack([np.interp(coord, offs, cols[:, ch]) for ch in range(3)], axis=2).astype(np.float32)
        err = np.abs(src[bg_mask].astype(np.float32) - model[bg_mask]).mean() if bg_mask.any() else 0
        if best is None or err < best[0]:
            best = (err, name, out, model)
    _, name, out, model = best
    stops_xml = ''.join('<stop offset="%.4f" stop-color="%s"/>' % (o, _hex(c)) for o, c in out)
    if name == 'r':
        defs = ('<radialGradient id="bg" gradientUnits="userSpaceOnUse" cx="%.1f" cy="%.1f" r="%.1f">%s</radialGradient>'
                % (W / 2, H / 2, axes['r'][1], stops_xml))
    else:
        x2, y2 = (1, 0) if name == 'x' else (0, 1)
        defs = '<linearGradient id="bg" x1="0" y1="0" x2="%d" y2="%d">%s</linearGradient>' % (x2, y2, stops_xml)
    return defs, model


def details(src, bg_mask, min_contrast=30):
    """Bright small features sitting on the background: glow dots -> circles, thin lines -> stroked segments."""
    lum = cv2.cvtColor(src, cv2.COLOR_RGB2GRAY).astype(np.float32)
    local = cv2.medianBlur(lum.astype(np.uint8), 31).astype(np.float32)
    bright = (bg_mask & (lum - local > min_contrast)).astype(np.uint8)
    count, labels, stats, cents = cv2.connectedComponentsWithStats(bright, 8)
    dots, line_mask = [], np.zeros_like(bright)
    for c in range(1, count):
        x, y, w, h, a = stats[c]
        if a < 4:
            continue
        circ = a / (math.pi * (max(w, h) / 2) ** 2 + 1e-6)
        if circ > 0.55 and max(w, h) < 40:
            m = labels == c
            colour = src[m].max(axis=0) if a < 30 else np.percentile(src[m], 90, axis=0)
            dots.append((cents[c][0], cents[c][1], max(w, h) / 2, colour))
        else:
            line_mask[labels == c] = 1
    lines = []
    if line_mask.any():
        dist = cv2.distanceTransform(line_mask, cv2.DIST_L2, 3)
        segs = cv2.HoughLinesP(line_mask * 255, 1, np.pi / 360, threshold=12, minLineLength=12, maxLineGap=3)
        for s in (segs if segs is not None else []):
            x1, y1, x2, y2 = s[0]
            n = int(max(abs(x2 - x1), abs(y2 - y1))) + 1
            xs, ys = np.linspace(x1, x2, n).astype(int), np.linspace(y1, y2, n).astype(int)
            width = max(0.8, 2 * float(np.median(dist[ys, xs])))
            colour = np.median(src[ys, xs], axis=0)
            lines.append((x1, y1, x2, y2, width, colour))
    return dots, lines


def _clusters(masks, gap=6):
    """Group region indices that touch (within gap px) into named clusters."""
    n = len(masks)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    boxes = []
    for m in masks:
        ys, xs = np.nonzero(m)
        boxes.append((xs.min(), ys.min(), xs.max(), ys.max()))
    for i in range(n):
        for j in range(i + 1, n):
            a, b = boxes[i], boxes[j]
            if a[0] - gap > b[2] or b[0] - gap > a[2] or a[1] - gap > b[3] or b[1] - gap > a[3]:
                continue
            di = cv2.dilate(masks[i].astype(np.uint8), np.ones((2 * gap + 1,) * 2, np.uint8)) > 0
            if (di & masks[j]).any():
                parent[find(i)] = find(j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def rebuild(src, ocr_records, label, font_config, out_dir, pt_per_px=None, locale='zh-TW', ocr=None, log=print):
    """src: HxWx3 uint8; label: HxW int partition (-1 = unassigned). Returns report dict."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    H, W = src.shape[:2]
    fonts = load_candidates(font_config, out_dir / 'fonts')
    by_ps = {f.postscript: f for f in fonts}
    report = {'size_px': [W, H]}

    # 1. text
    specs, glyph_union, text_report = analyse_text(src, ocr_records, fonts, locale)
    report['text'] = text_report
    log('text: %d live of %d lines' % (len(specs), len(text_report)))
    # 1b. lettering on circles (badges): unwrap and OCR the ring
    arc_frames = []
    if ocr is not None:
        geos = []
        for k in np.unique(label):
            if k < 0:
                continue
            m = label == k
            if m.sum() > 3000:
                filled = m.copy()
                cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
                filled = cv2.drawContours(np.zeros(m.shape, np.uint8), cnts, -1, 1, -1) > 0
                g = fit_geometry(filled)
                if g and g[0] == 'ellipse':
                    geos.append((g[0], g[1]))
        circles = badge_candidates(geos)
        arc_frames, lettering = arc_text(src, circles, ocr, fonts, locale)
        glyph_union = glyph_union | lettering
        log('badges: %d, arc characters: %d' % (len(circles), len(arc_frames)))
        report['arc_text'] = sorted({f['arc'] for f in arc_frames})
    tmask = cv2.dilate(glyph_union.astype(np.uint8), np.ones((9, 9), np.uint8))
    # Drop shadows and soft edges in the glyph colour sit just outside the glyph mask; remove them too.
    for s in specs:
        x0, y0 = max(0, int(s['x'] - s['h'] * .3)), max(0, int(s['y'] - s['h'] * .3))
        x1, y1 = min(W, int(s['x'] + s['w'] + s['h'] * .3)), min(H, int(s['y'] + s['h'] * 1.3))
        if x1 <= x0 or y1 <= y0 or 'frame' in s:
            continue
        g = glyph_union[y0:y1, x0:x1].astype(np.uint8)
        if not g.any():
            continue
        reach = max(3, int(s['h'] * 0.12))
        near = cv2.distanceTransform(1 - g, cv2.DIST_L2, 3) < reach
        ink = np.array([int(s['fill'][i:i + 2], 16) for i in (1, 3, 5)], np.float32)
        close = np.linalg.norm(src[y0:y1, x0:x1].astype(np.float32) - ink, axis=2) < 90
        extra = cv2.dilate((near & close).astype(np.uint8), np.ones((5, 5), np.uint8))
        tmask[y0:y1, x0:x1] |= extra
    clean = cv2.inpaint(src[:, :, ::-1], tmask, 7, cv2.INPAINT_TELEA)[:, :, ::-1]

    # 2. background
    label = label.copy()
    # Regions that are mostly lettering (the segmenter often cuts out single glyphs) join the region around them,
    # otherwise they leave glyph-shaped holes and ghosts once the text is live.
    boxes = [(s['x'], s['y'], s['x'] + s['w'], s['y'] + s['h']) for s in specs if 'frame' not in s]

    def inside_text_box(m):
        ys, xs = np.nonzero(m)
        for x0, y0, x1, y1 in boxes:
            pad = 0.1 * (y1 - y0)
            inside = ((xs >= x0 - pad) & (xs <= x1 + pad) & (ys >= y0 - pad) & (ys <= y1 + pad)).mean()
            if inside > 0.85 and len(xs) < 0.5 * (x1 - x0) * (y1 - y0):
                return True
        return False
    textish = [k for k in np.unique(label) if k >= 0 and ((tmask[label == k] > 0).mean() > 0.5 or inside_text_box(label == k))]
    for k in sorted(textish, key=lambda k: (label == k).sum()):
        reg = label == k
        ring = cv2.dilate(reg.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool) & ~reg
        vals = label[ring]
        vals = vals[~np.isin(vals, textish)]
        label[reg] = np.bincount(vals + 1).argmax() - 1 if len(vals) else -1
    ids = [k for k in np.unique(label) if k >= 0]
    areas = {k: int((label == k).sum()) for k in ids}
    bg_ids = []
    for k in sorted(ids, key=lambda k: -areas[k]):
        m = label == k
        border = m[0].any() or m[-1].any() or m[:, 0].any() or m[:, -1].any()
        if border and areas[k] > 0.01 * H * W and np.abs(clean[m].astype(float) - clean[m].mean(0)).mean() < 25:
            bg_ids.append(k)
    bg_mask = (label < 0) | np.isin(label, bg_ids)
    bg_defs, bg_model = background_gradient(clean, bg_mask)
    defs = [bg_defs]
    layers = {'01_Background': ['<rect id="BACKGROUND" x="0" y="0" width="%d" height="%d" fill="url(#bg)"/>' % (W, H)]}

    # 3. regions -> shapes (background pieces only when the band model is not close enough)
    elements, region_masks, region_meta = [], [], []
    stats = {'ellipse': 0, 'polygon': 0, 'path': 0, 'traced': 0, 'skipped_bg': 0}
    order = sorted(ids, key=lambda k: -areas[k])
    for k in order:
        m = label == k
        if areas[k] < 40:
            continue
        # Background pieces, and inner regions that the background model already paints, need no shape.
        err = np.abs(clean[m].astype(float) - bg_model[m]).mean()
        if err < (10 if k in bg_ids else 7):
            stats['skipped_bg'] += 1
            continue
        geo = fit_geometry(m)
        if geo is None:
            continue
        kind, g, iou = geo
        gid = 'g%d' % k
        valid = m & (tmask == 0) if (m & (tmask == 0)).sum() > 0.3 * m.sum() else m
        pdefs, fill, perr, pkind = fit_paint(clean, valid, gid)
        eid = 'R%03d' % k
        if perr > 16 and areas[k] > 400:
            el, npaths = trace_region(clean, m, eid)
            stats['traced'] += 1
            region_meta.append({'id': eid, 'kind': 'traced', 'paths': npaths, 'paint_error': round(float(perr), 1)})
        else:
            el = shape_element(kind, g, fill, eid)
            if pdefs:
                defs.append(pdefs)
            stats[kind] += 1
            region_meta.append({'id': eid, 'kind': kind, 'iou': round(iou, 3), 'paint': pkind, 'paint_error': round(float(perr), 1)})
        elements.append(el)
        region_masks.append(m)
    log('regions: %s' % stats)
    # 5a. text frames (needed now to decide which artwork sits under text that is itself behind other artwork)
    frames = []
    for s in specs:
        font = by_ps[s['font']]
        m = page_matrix(s, fit_matrix(font, s['text'], (s['x'], s['y'], s['w'], s['h']), s['skew'], s['tracking']))
        frames.append(dict(s, m=m))
    frames += arc_frames
    under = set()
    near_ink = cv2.dilate(glyph_union.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    for f in frames:
        if not f.get('behind'):
            continue
        R = rasterise(by_ps[f['font']], f['text'], NOMINAL, f['m'], (H, W), (0, 0), f.get('tracking', 0))
        for i, m in enumerate(region_masks):
            hit = (m & R).sum()
            # a region that covers the text where no glyph is visible is in front; one that holds the visible
            # glyphs (or their surroundings) is underneath
            if hit > 0.02 * R.sum() and (m & R & ~near_ink).sum() < 0.5 * hit:
                under.add(i)
    layers['01a_Art_under_text'] = [elements[i] for i in sorted(under)]
    rest = [i for i in range(len(region_masks)) if i not in under]
    groups = [[rest[j] for j in g] for g in _clusters([region_masks[i] for i in rest])] if rest else []
    groups.sort(key=lambda g: min(order.index(int(region_meta[i]['id'][1:])) for i in g))
    for gi, g in enumerate(groups):
        name = '02_Group_%02d' % (gi + 1)
        layers[name] = [elements[i] for i in sorted(g)]
    # 4. details on the background
    dots, lines = details(clean, bg_mask & ~np.isin(label, [k for k in ids if k not in bg_ids]) & (tmask == 0))
    det = []
    for n, (x1, y1, x2, y2, w, c) in enumerate(lines):
        det.append('<path id="LINE_%03d" d="M%.1f %.1fL%.1f %.1f" fill="none" stroke="%s" stroke-width="%.2f" stroke-linecap="round"/>' % (n, x1, y1, x2, y2, _hex(c), w))
    for n, (x, y, r, c) in enumerate(dots):
        gid = 'dot%d' % n
        defs.append('<radialGradient id="%s"><stop offset="0" stop-color="%s"/><stop offset=".35" stop-color="%s" stop-opacity=".85"/><stop offset="1" stop-color="%s" stop-opacity="0"/></radialGradient>' % (gid, _hex(np.maximum(c, 230)), _hex(c), _hex(c)))
        det.append('<circle id="DOT_%03d" cx="%.1f" cy="%.1f" r="%.1f" fill="url(#%s)"/>' % (n, x, y, r * 1.6, gid))
    layers['03_Lines_and_dots'] = det
    report['details'] = {'dots': len(dots), 'lines': len(lines)}

    # 5. text frames
    # Text partly hidden by artwork (behind a tape or banner) goes under the artwork layers.
    behind = [f for f in frames if f.get('behind')]
    front = [f for f in frames if not f.get('behind')]
    decos_b, texts_b = text_layer(behind, fonts)
    decos, texts = text_layer(front, fonts)
    layers = dict([('01_Background', layers.pop('01_Background')), ('01a_Art_under_text', layers.pop('01a_Art_under_text')),
                   ('01b_Text_behind_art', decos_b + texts_b)] + list(layers.items()))
    layers['04_Text_decorations'] = decos
    layers['05_Live_text'] = texts

    # 6. assemble (page units = source px scaled by pt_per_px)
    k = pt_per_px or 1.0
    body = ''.join('<g id="%s">%s</g>' % (name, ''.join(els)) for name, els in layers.items() if els)
    svg = ('<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="%.2f" height="%.2f" viewBox="0 0 %d %d">'
           '<defs>%s</defs>%s</svg>' % (W * k, H * k, W, H, ''.join(defs), body))
    (out_dir / 'rebuild.svg').write_text(svg)
    def doc(names):
        part = ''.join('<g id="%s">%s</g>' % (n, ''.join(e)) for n, e in layers.items() if e and n in names)
        return ('<svg xmlns="http://www.w3.org/2000/svg" width="%.2f" height="%.2f" viewBox="0 0 %d %d"><defs>%s</defs>%s</svg>'
                % (W * k, H * k, W, H, ''.join(defs), part))
    lower = [n for n in layers if n.startswith('01')]
    stages = [(doc(lower), [f for f in frames if f.get('behind')]),
              (doc([n for n in layers if n not in lower]), [f for f in frames if not f.get('behind')])]
    write_pdf([(art, [dict(f, m=tuple(v * k for v in f['m'])) for f in fr]) for art, fr in stages], fonts,
              (W * k, H * k), out_dir / 'rebuild.pdf', out_dir)
    (out_dir / 'rebuild.ai').write_bytes((out_dir / 'rebuild.pdf').read_bytes())
    report['regions'] = region_meta
    report['region_stats'] = stats
    report['layers'] = {n: len(e) for n, e in layers.items()}
    report['text_frames'] = len(frames)
    report['score'] = score(out_dir / 'rebuild.pdf', src)
    (out_dir / 'rebuild-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, default=float))
    return report


def score(pdf, src):
    import pymupdf
    H, W = src.shape[:2]
    with pymupdf.open(pdf) as d:
        page = d[0]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(W / page.rect.width, H / page.rect.height), alpha=False)
        r = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)[:H, :W]
        drawings = len(page.get_drawings())
        images = len(page.get_images())
    diff = np.abs(r.astype(float) - src.astype(float))
    return {'mae_rgb': [round(float(v), 2) for v in diff.mean((0, 1))], 'rmse': round(float(np.sqrt((diff ** 2).mean())), 2),
            'pixels_over_32': round(float((diff.max(2) > 32).mean()), 4), 'vector_drawings': drawings, 'images': images}
