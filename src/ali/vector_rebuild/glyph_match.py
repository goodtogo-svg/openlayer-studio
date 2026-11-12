"""Closest installed face by letter shapes.

A whole-line fit lets any face reach a box by stretching and letter-spacing, so it cannot tell faces apart on small
lettering. Here each visible letter of the source is cut out on its own and compared with the same letter of a
candidate face at that face's own proportions: shape (normalised overlap) and width-to-height ratio, after one common
width factor per line, which costs like any other distortion. Spacing and kerning do not enter."""
import math

import cv2
import numpy as np

from .text import rasterise, NOMINAL, fit_matrix

GRID = 40
_cache = {}


def _norm(B):
    ys, xs = np.nonzero(B)
    c = B[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.float32)
    return cv2.resize(c, (GRID, GRID), interpolation=cv2.INTER_AREA)


def face_glyph(font, ch):
    key = (font.postscript, ch)
    if key not in _cache:
        x0, y0, x1, y1 = font.ink(ch, NOMINAL, (1, 0, 0, 1, 0, 0))
        h = 64.0
        w = max(2.0, (x1 - x0) * h / max(1e-6, y1 - y0))
        m = fit_matrix(font, ch, (2, 2, w, h), 0)
        R = rasterise(font, ch, NOMINAL, m, (int(h) + 5, int(w) + 5))
        _cache[key] = (_norm(R), w / h) if R.any() else None
    return _cache[key]


def source_glyphs(G, V, text):
    """One (shape, w/h) per letter of text, left to right, None for a letter touching hidden area; None when the
    visible ink does not split into exactly one piece per letter."""
    n = len(text.replace(' ', ''))
    num, lab, st, _ = cv2.connectedComponentsWithStats(G.astype(np.uint8), 8)
    comps = [k for k in range(1, num) if st[k, 4] > 15]
    if n < 2 or len(comps) != n:
        return None
    comps.sort(key=lambda k: st[k, 0])
    hid = cv2.dilate((~V).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    out = []
    for k in comps:
        mk = lab == k
        out.append(None if (mk & hid).any() else (_norm(mk), st[k, 2] / st[k, 3]))
    return out


def similarity(font, text, glyphs, distort_cost):
    """Mean letter likeness (0..1) of font to the source letters, less the cost of the common width factor."""
    chars = text.replace(' ', '')
    pairs = []
    for s, c in zip(glyphs, chars):
        if s is None or not c.isalnum() or not font.covers(c):
            continue
        g = face_glyph(font, c)
        if g is not None:
            pairs.append((s, g))
    if len(pairs) < 2:
        return None
    k = math.exp(float(np.median([math.log(s[1] / g[1]) for s, g in pairs])))
    sc = []
    for (sn, sa), (gn, ga) in pairs:
        ga *= k
        sc.append(np.minimum(sn, gn).sum() / max(1e-6, np.maximum(sn, gn).sum()) * min(sa, ga) / max(sa, ga))
    return float(np.mean(sc)) - distort_cost * abs(math.log(k))


def rank(fonts, text, glyphs, distort_cost):
    """[(similarity, font)] best first."""
    out = []
    for f in fonts:
        if f.covers(text.replace(' ', '')):
            s = similarity(f, text, glyphs, distort_cost)
            if s is not None:
                out.append((s, f))
    out.sort(key=lambda r: (-r[0], r[1].postscript))
    return out
