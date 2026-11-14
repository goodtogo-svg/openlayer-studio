"""Shape recognition and fill fitting for segmented regions -> clean SVG elements.

Each region (a boolean mask from the segmentation partition) becomes, in order of preference:
an ellipse/circle, a polygon (triangles, chevrons, quads), or a smooth Bezier outline; holes become
even-odd subpaths. Its paint is a flat colour, a linear or a radial gradient, chosen by fit quality.
Regions whose colour cannot be modelled are traced as multi-colour vectors instead.
"""
import io
import math

import cv2
import numpy as np


def _hex(c):
    return '#%02x%02x%02x' % tuple(int(np.clip(round(v), 0, 255)) for v in c)


def _iou(a, b):
    return (a & b).sum() / max(1, (a | b).sum())


# ---------------------------------------------------------------- paint
def fit_paint(src, mask, gid):
    """Return (defs_xml, fill_attr, quality, kind). quality = mean abs RGB error of the model."""
    ys, xs = np.nonzero(mask)
    px = src[ys, xs].astype(np.float64)
    mean = px.mean(0)
    flat_err = np.abs(px - mean).mean()
    best = (flat_err, None, _hex(mean), 'flat')
    if len(xs) < 30 or flat_err < 6:
        return '', best[2], flat_err, 'flat'
    # linear: colour = a + b*t along the best direction
    cx, cy = xs.mean(), ys.mean()
    for ang in range(0, 180, 15):
        r = math.radians(ang)
        t = (xs - cx) * math.cos(r) + (ys - cy) * math.sin(r)
        lo, hi = np.percentile(t, 1), np.percentile(t, 99)
        if hi - lo < 4:
            continue
        u = np.clip((t - lo) / (hi - lo), 0, 1)
        # three stops (0, .5, 1) with piecewise-linear interpolation
        basis = np.stack([np.clip(1 - 2 * u, 0, 1), 1 - np.abs(2 * u - 1), np.clip(2 * u - 1, 0, 1)], 1)
        stops, *_ = np.linalg.lstsq(basis, px, rcond=None)
        err = np.abs(basis @ stops - px).mean()
        if err < best[0] - 1.5:
            x1, y1 = cx + lo * math.cos(r), cy + lo * math.sin(r)
            x2, y2 = cx + hi * math.cos(r), cy + hi * math.sin(r)
            defs = ('<linearGradient id="%s" gradientUnits="userSpaceOnUse" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f">'
                    '<stop offset="0" stop-color="%s"/><stop offset=".5" stop-color="%s"/><stop offset="1" stop-color="%s"/></linearGradient>'
                    % (gid, x1, y1, x2, y2, _hex(stops[0]), _hex(stops[1]), _hex(stops[2])))
            best = (err, defs, 'url(#%s)' % gid, 'linear')
    # radial from the centroid
    d = np.hypot(xs - cx, ys - cy)
    rmax = np.percentile(d, 99)
    if rmax > 3:
        u = np.clip(d / rmax, 0, 1)
        basis = np.stack([np.clip(1 - 2 * u, 0, 1), 1 - np.abs(2 * u - 1), np.clip(2 * u - 1, 0, 1)], 1)
        stops, *_ = np.linalg.lstsq(basis, px, rcond=None)
        err = np.abs(basis @ stops - px).mean()
        if err < best[0] - 1.5:
            defs = ('<radialGradient id="%s" gradientUnits="userSpaceOnUse" cx="%.1f" cy="%.1f" r="%.1f">'
                    '<stop offset="0" stop-color="%s"/><stop offset=".5" stop-color="%s"/><stop offset="1" stop-color="%s"/></radialGradient>'
                    % (gid, cx, cy, rmax, _hex(stops[0]), _hex(stops[1]), _hex(stops[2])))
            best = (err, defs, 'url(#%s)' % gid, 'radial')
    err, defs, fill, kind = best
    return defs or '', fill, err, kind


# ---------------------------------------------------------------- geometry
def _bezier_path(contour, eps):
    """Closed contour -> smooth cubic Bezier path (Catmull-Rom through simplified points)."""
    pts = cv2.approxPolyDP(contour, eps, True)[:, 0, :].astype(float)
    n = len(pts)
    if n < 3:
        return None
    # keep sharp corners sharp: angle test
    out = ['M%.1f %.1f' % tuple(pts[0])]
    for i in range(n):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        v1, v2 = p1 - p0, p2 - p1
        v3 = p3 - p2
        def sharp(a, b):
            na, nb = np.linalg.norm(a), np.linalg.norm(b)
            return na and nb and np.dot(a, b) / (na * nb) < 0.5
        c1 = p1 if sharp(v1, v2) else p1 + (p2 - p0) / 6
        c2 = p2 if sharp(v2, v3) else p2 - (p3 - p1) / 6
        out.append('C%.1f %.1f %.1f %.1f %.1f %.1f' % (c1[0], c1[1], c2[0], c2[1], p2[0], p2[1]))
    return ''.join(out) + 'Z'


def rasterise_svg_path(d, shape, even_odd=True):
    """Rasterise an SVG path string (only M, L, C, Z absolute) with cairo for IoU checks."""
    import re
    import cairocffi as cairo
    h, w = shape
    surf = cairo.ImageSurface(cairo.FORMAT_A8, w, h)
    ctx = cairo.Context(surf)
    for cmd, args in re.findall(r'([MLCZ])([^MLCZ]*)', d):
        v = [float(x) for x in args.replace(',', ' ').split()]
        if cmd == 'M':
            ctx.move_to(*v)
        elif cmd == 'L':
            ctx.line_to(*v)
        elif cmd == 'C':
            ctx.curve_to(*v)
        else:
            ctx.close_path()
    ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD if even_odd else cairo.FILL_RULE_WINDING)
    ctx.fill()
    surf.flush()
    a = np.frombuffer(surf.get_data(), np.uint8).reshape(h, surf.get_stride())[:, :w]
    return a > 127


def fit_geometry(mask):
    """Return (kind, svg_attrs_or_d, iou). kind in ellipse | polygon | path."""
    m8 = mask.astype(np.uint8)
    contours, hier = cv2.findContours(m8, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    outer = [i for i in range(len(contours)) if hier[0][i][3] < 0]
    holes = [i for i in range(len(contours)) if hier[0][i][3] >= 0 and cv2.contourArea(contours[i]) > 20]
    H, W = mask.shape
    if len(outer) == 1 and not holes:
        c = contours[outer[0]]
        if len(c) >= 5:
            (ex, ey), (ma, mb), ang = cv2.fitEllipse(c)
            em = np.zeros_like(m8)
            cv2.ellipse(em, ((ex, ey), (ma, mb), ang), 1, -1)
            iou = _iou(em > 0, mask)
            if iou > 0.94:
                return 'ellipse', {'cx': ex, 'cy': ey, 'rx': ma / 2, 'ry': mb / 2, 'angle': ang}, float(iou)
        peri = cv2.arcLength(c, True)
        poly = cv2.approxPolyDP(c, 0.012 * peri, True)
        if 3 <= len(poly) <= 10:
            pm = np.zeros_like(m8)
            cv2.fillPoly(pm, [poly], 1)
            iou = _iou(pm > 0, mask)
            if iou > 0.93:
                d = 'M' + 'L'.join('%.1f %.1f' % (p[0][0], p[0][1]) for p in poly) + 'Z'
                return 'polygon', d, float(iou)
    parts = []
    for i in outer + holes:
        c = contours[i]
        eps = max(0.8, 0.004 * cv2.arcLength(c, True))
        d = _bezier_path(c, eps)
        if d:
            parts.append(d)
    if not parts:
        return None
    d = ''.join(parts)
    iou = _iou(rasterise_svg_path(d, (H, W)), mask)
    return 'path', d, float(iou)


def shape_element(kind, geo, fill, eid, extra=''):
    if kind == 'ellipse':
        g = geo
        rot = ' transform="rotate(%.2f %.1f %.1f)"' % (g['angle'] - 90, g['cx'], g['cy']) if abs(g['rx'] - g['ry']) > 0.5 else ''
        # cv2 angle is for the first axis (width); rotate(-90) maps it to SVG's rx along x after swap
        rx, ry = (g['ry'], g['rx']) if rot else (g['rx'], g['ry'])
        if abs(rx - ry) <= 0.5:
            return '<circle id="%s" cx="%.1f" cy="%.1f" r="%.1f" fill="%s"%s/>' % (eid, g['cx'], g['cy'], (rx + ry) / 2, fill, extra)
        return '<ellipse id="%s" cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="%s"%s%s/>' % (eid, g['cx'], g['cy'], rx, ry, fill, rot, extra)
    return '<path id="%s" d="%s" fill="%s" fill-rule="evenodd"%s/>' % (eid, geo, fill, extra)


# ---------------------------------------------------------------- multi-colour fallback
def trace_region(src, mask, eid, scale=2):
    """vtracer fallback for regions with detail no paint model can hold; returns a <g> in page coordinates."""
    import re
    import vtracer
    from PIL import Image
    ys, xs = np.nonzero(mask)
    x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    rgba = np.dstack([src[y0:y1, x0:x1], (mask[y0:y1, x0:x1] * 255).astype(np.uint8)])
    im = Image.fromarray(rgba).resize(((x1 - x0) * scale, (y1 - y0) * scale), Image.NEAREST)
    buf = io.BytesIO()
    im.save(buf, 'PNG')
    svg = vtracer.convert_raw_image_to_svg(buf.getvalue(), img_format='png', colormode='color', hierarchical='stacked',
                                           mode='spline', filter_speckle=4 * scale, color_precision=6, layer_difference=16,
                                           corner_threshold=60, length_threshold=4.0, max_iterations=10, splice_threshold=45, path_precision=1)
    paths = re.findall(r'<path[^>]*/>', svg)
    body = ''.join(paths)
    return '<g id="%s" transform="translate(%d %d) scale(%s)">%s</g>' % (eid, x0, y0, 1 / scale, body), len(paths)


def residual_detail(src, rendered, keep_out, threshold=24, tile=384, scale=2):
    """Vector paths for what a rebuild still misses: pixels where the render is more than threshold off the
    source (per channel), outside keep_out (live text), traced tile by tile in the source colours.
    Returns ([svg elements], path count, covered fraction)."""
    import re
    import vtracer
    from PIL import Image
    H, W = src.shape[:2]
    res = np.abs(src.astype(np.int16) - rendered.astype(np.int16)).max(2) > threshold
    res &= ~keep_out
    res = cv2.morphologyEx(res.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    res = cv2.dilate(res, np.ones((3, 3), np.uint8))
    # close small gaps so shading comes out as whole patches, not a speckle of single pixels
    res = cv2.morphologyEx(res, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8)) > 0
    res &= ~keep_out
    els, count = [], 0
    for ty in range(0, H, tile):
        for tx in range(0, W, tile):
            y1, x1 = min(H, ty + tile), min(W, tx + tile)
            m = res[ty:y1, tx:x1]
            if m.sum() < 20:
                continue
            rgba = np.dstack([src[ty:y1, tx:x1], (m * 255).astype(np.uint8)])
            im = Image.fromarray(rgba).resize(((x1 - tx) * scale, (y1 - ty) * scale), Image.NEAREST)
            buf = io.BytesIO()
            im.save(buf, 'PNG')
            svg = vtracer.convert_raw_image_to_svg(buf.getvalue(), img_format='png', colormode='color', hierarchical='cutout',
                                                   mode='spline', filter_speckle=2 * scale, color_precision=7, layer_difference=8,
                                                   corner_threshold=60, length_threshold=4.0, max_iterations=10,
                                                   splice_threshold=45, path_precision=1)
            paths = re.findall(r'<path[^>]*/>', svg)
            if paths:
                count += len(paths)
                els.append('<g id="DETAIL_%d_%d" transform="translate(%d %d) scale(%s)">%s</g>'
                           % (ty // tile, tx // tile, tx, ty, 1 / scale, ''.join(paths)))
    return els, count, float(res.mean())
