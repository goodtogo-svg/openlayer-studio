"""Lettering set on a circle (badges, seals): polar-unwrap the ring, OCR the strip, emit per-character live text."""
import math

import cv2
import numpy as np

from .text import NOMINAL, normalise_text


def unwrap(src, cx, cy, r_in, r_out, side, width=1200):
    """side 'top': angles 180..360 (left -> over the top -> right), outer radius at the top row.
    side 'bottom': angles 180..0 (left -> under -> right), outer radius at the bottom row."""
    rows = max(8, int(r_out - r_in))
    angles = np.linspace(180, 360, width) if side == 'top' else np.linspace(180, 0, width)
    radii = np.linspace(r_out, r_in, rows) if side == 'top' else np.linspace(r_in, r_out, rows)
    A, R = np.meshgrid(np.radians(angles), radii)
    mx = (cx + R * np.cos(A)).astype(np.float32)
    my = (cy + R * np.sin(A)).astype(np.float32)
    return cv2.remap(src, mx, my, cv2.INTER_LINEAR), angles, radii


def badge_candidates(regions, min_r=40):
    """regions: list of (kind, geo) from fit_geometry. Near-circular ellipses large enough to carry lettering."""
    out = []
    for kind, g in regions:
        if kind == 'ellipse' and min(g['rx'], g['ry']) > min_r and abs(g['rx'] - g['ry']) < 0.06 * max(g['rx'], g['ry']):
            out.append((g['cx'], g['cy'], (g['rx'] + g['ry']) / 2))
    # keep the outermost of concentric circles
    out.sort(key=lambda c: -c[2])
    kept = []
    for c in out:
        if not any(math.hypot(c[0] - k[0], c[1] - k[1]) < 0.2 * k[2] for k in kept):
            kept.append(c)
    return kept


def arc_text(src, circles, ocr, fonts, locale='zh-TW', min_conf=0.8):
    """ocr(list_of_images) -> list of record lists. Returns (frames, mask_of_lettering)."""
    H, W = src.shape[:2]
    jobs = []
    for ci, (cx, cy, r) in enumerate(circles):
        for side in ('top', 'bottom'):
            strip, angles, radii = unwrap(src, cx, cy, 0.55 * r, 1.0 * r, side, width=int(math.pi * 0.78 * r))
            # upscale small strips so the recogniser has enough pixels per glyph
            scale = max(1.0, 48 / strip.shape[0])
            jobs.append((ci, side, cx, cy, r, angles, radii, scale, cv2.resize(strip, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)))
    if not jobs:
        return [], np.zeros((H, W), bool)
    results = ocr([j[-1] for j in jobs])
    frames, lettering = [], np.zeros((H, W), bool)
    for job, recs in zip(jobs, results):
        ci, side, cx, cy, r, angles, radii, scale, strip = job
        for rec in recs:
            text = normalise_text(rec['text'], locale) if rec['confidence'] >= min_conf else ''
            if len(text.replace(' ', '')) < 2:
                continue
            poly = np.array(rec['polygon'], float) / scale
            x0, x1 = poly[:, 0].min(), poly[:, 0].max()
            y0, y1 = poly[:, 1].min(), poly[:, 1].max()
            n = len(angles)
            a0 = angles[int(np.clip(x0, 0, n - 1))]
            a1 = angles[int(np.clip(x1, 0, n - 1))]
            # glyph band inside the ring: rows between y0..y1 -> radii
            ra = radii[int(np.clip(y0, 0, len(radii) - 1))]
            rb = radii[int(np.clip(y1, 0, len(radii) - 1))]
            r_mid = (ra + rb) / 2
            cap = abs(ra - rb)
            latin = all(ord(c) < 0x2e80 for c in text)
            font = next((f for f in fonts if f.covers(text.replace(' ', '')) and (not f.is_cjk) == latin), None) or \
                next((f for f in fonts if f.covers(text.replace(' ', ''))), None)
            if font is None:
                continue
            # cap height of the font -> point size
            _, ty0, _, ty1 = font.ink('H' if latin else '國', NOMINAL, (1, 0, 0, 1, 0, 0))
            size = cap / ((ty1 - ty0) / NOMINAL) * 0.92
            colour = _ink_colour(strip, rec['polygon'])
            name = 'ARC_%d_%s' % (ci, side.upper())
            centres = _glyph_centres(strip, rec['polygon'], colour, len(text.replace(' ', '')))
            glyphs = [c for c in text if c != ' ']
            for i, ch in enumerate(glyphs):
                if centres is not None:
                    ang = angles[int(np.clip(centres[i] / scale, 0, n - 1))]
                else:
                    ang = a0 + (a1 - a0) * (i + .5) / len(glyphs)
                rad = math.radians(ang)
                rot = ang + 90 if side == 'top' else ang - 90
                t = math.radians(rot)
                m = (math.cos(t), math.sin(t), -math.sin(t), math.cos(t), 0, 0)
                bx0, by0, bx1, by1 = font.ink(ch, size, m)
                px, py = cx + r_mid * math.cos(rad), cy + r_mid * math.sin(rad)
                frames.append({'name': '%s_%02d' % (name, i), 'text': ch, 'font': font.postscript, 'size': size,
                               'm': (m[0] * size / NOMINAL, m[1] * size / NOMINAL, m[2] * size / NOMINAL, m[3] * size / NOMINAL,
                                     px - (bx0 + bx1) / 2, py - (by0 + by1) / 2),
                               'fill': colour, 'runs': [], 'tracking': 0, 'arc': name})
            # lettering pixels to remove from the artwork
            band = np.zeros((H, W), np.uint8)
            lo, hi = sorted((ra, rb))
            cv2.ellipse(band, (int(cx), int(cy)), (int(hi + 3), int(hi + 3)), 0, min(a0, a1) - 3, max(a0, a1) + 3, 1, -1)
            cv2.ellipse(band, (int(cx), int(cy)), (int(max(1, lo - 3)), int(max(1, lo - 3))), 0, 0, 360, 0, -1)
            d = np.linalg.norm(src.astype(np.float32) - np.array([int(colour[i:i + 2], 16) for i in (1, 3, 5)], np.float32), axis=2)
            lettering |= (band > 0) & (d < 80)
    return frames, lettering


def _ink_colour(strip, polygon):
    poly = np.array(polygon, np.int32)
    m = np.zeros(strip.shape[:2], np.uint8)
    cv2.fillPoly(m, [poly], 1)
    px = strip[m > 0].astype(np.float32)
    if len(px) < 10:
        return '#ffffff'
    _, labels, centres = cv2.kmeans(px, 2, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1), 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=2)
    ink = centres[int(np.argmin(counts))]   # lettering is the minority inside its box
    return '#%02x%02x%02x' % tuple(int(np.clip(v, 0, 255)) for v in ink)


def subprocess_ocr(python, work_dir):
    """OCR callback that runs ocr_batch.py in another interpreter (the OCR model environment)."""
    import json
    import subprocess
    from pathlib import Path
    from PIL import Image

    def run(images):
        work = Path(work_dir)
        work.mkdir(parents=True, exist_ok=True)
        paths = []
        for i, im in enumerate(images):
            p = work / ('strip-%02d.png' % i)
            Image.fromarray(im).save(p)
            paths.append(str(p))
        out = work / 'strips-ocr.json'
        subprocess.run([python, str(Path(__file__).with_name('ocr_batch.py')), str(out)] + paths, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return json.loads(out.read_text())
    return run


def _glyph_centres(strip, polygon, colour, count):
    """x centres of the lettering's glyphs in the strip (connected components of the ink colour), if they
    split into exactly `count` glyphs; this keeps the designer's spacing and word gaps."""
    poly = np.array(polygon, np.int32)
    box = np.zeros(strip.shape[:2], np.uint8)
    cv2.fillPoly(box, [poly], 1)
    ink = np.array([int(colour[i:i + 2], 16) for i in (1, 3, 5)], np.float32)
    m = ((np.linalg.norm(strip.astype(np.float32) - ink, axis=2) < 70) & (box > 0)).astype(np.uint8)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(m, 8)
    spans = sorted([(stats[c][0], stats[c][0] + stats[c][2]) for c in range(1, n) if stats[c][4] >= 6])
    merged = []
    for a, b in spans:   # merge pieces that overlap in x (dots, broken strokes)
        if merged and a < merged[-1][1] - 1:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    if len(merged) != count:
        return None
    return [(a + b) / 2 for a, b in merged]
