"""PDF to native Adobe Illustrator (.ai) compiler.

The compiler has three stages:

1. Front end (this module, Python): read every page of an unlocked PDF with PyMuPDF and lower its display list to a
   small intermediate representation (``ali-pdf-ai/1``): bezier paths with their paint, placed images with their
   transform, and text runs with both a live-text description and, where the embedded font allows it, glyph outlines.
   Items keep the PDF paint order (``seqno``), so stacking is preserved.
2. Back end (generated ExtendScript): ``build-illustrator.jsx`` reads ``compile-ir.json`` next to it and builds one
   Illustrator document, one artboard and one layer per PDF page, using native pathItems, compoundPathItems,
   textFrames and embedded raster images. It refuses to overwrite and writes ``build-report.json``.
3. Verification: ``verify-illustrator.jsx`` reopens the saved .ai, counts native objects and exports one PNG per
   artboard; ``verify_job`` compares those PNGs against PyMuPDF renders of the source and records the result.

Nothing here claims a native success by itself. The .ai only counts once Illustrator saved and reopened it, and the
visual comparison is evidence for a reviewer, not an approval.
"""
import io
import json
import math
from pathlib import Path

from .runtime import atomic_json, digest

SCHEMA = 'ali-pdf-ai/1'
TEXT_MODES = ('auto', 'live', 'outline', 'keep')
ENGINES = ('illustrator-import', 'ir')
COLOR_MODES = ('auto', 'rgb', 'cmyk')
PAGE_GAP_PT = 36.0
_EPS = 1e-3
_HANDLED_LOG = {'fill-path', 'stroke-path', 'fill-text', 'stroke-text', 'ignore-text', 'fill-image'}


# --------------------------------------------------------------------------------------------- small helpers
def _r(v):
    return round(float(v), 3)


def _strip_subset(name):
    name = name or ''
    if len(name) > 7 and name[6] == '+' and name[:6].isupper() and name[:6].isalpha():
        return name[7:]
    return name


def _postscript_guess(name):
    """Best-effort PostScript name for Illustrator's textFonts.getByName()."""
    return _strip_subset(name).replace(' ', '')


def _color(components):
    """PDF colour components -> IR colour (keeps the source colour space for Illustrator to convert)."""
    if components is None:
        return None
    c = [max(0.0, min(1.0, float(x))) for x in components]
    if len(c) == 1:
        return {'space': 'gray', 'v': [_r(c[0])]}
    if len(c) == 3:
        return {'space': 'rgb', 'v': [_r(x) for x in c]}
    if len(c) == 4:
        return {'space': 'cmyk', 'v': [_r(x) for x in c]}
    return None


def _parse_dashes(text):
    try:
        body, phase = text.rsplit(']', 1)
        values = [float(x) for x in body.strip(' [').split()]
        return [_r(x) for x in values if x >= 0], _r(float(phase or 0))
    except (ValueError, AttributeError):
        return [], 0.0


class _Space:
    """Unrotated PDF page coordinates (y down) -> Illustrator document coordinates (y up)."""

    def __init__(self, page, offset_x):
        self.matrix = page.rotation_matrix
        self.offset_x = offset_x

    def point(self, x, y):
        m = self.matrix
        vx = m.a * x + m.c * y + m.e
        vy = m.b * x + m.d * y + m.f
        return [_r(self.offset_x + vx), _r(-vy)]

    def visual(self, x, y):
        """Visual (already rotated) page coordinates, y down -> Illustrator coordinates."""
        return [self.offset_x + x, -y]

    def vector(self, x, y):
        """Rotate a direction vector into visual page space (y still down)."""
        m = self.matrix
        return m.a * x + m.c * y, m.b * x + m.d * y


# --------------------------------------------------------------------------------------------- paths
def _same(a, b):
    return abs(a[0] - b[0]) < _EPS and abs(a[1] - b[1]) < _EPS


def _finish(subpath, force_close=False):
    pts = subpath['points']
    if len(pts) > 2 and _same(pts[0][:2], pts[-1][:2]):
        last = pts.pop()
        pts[0][2], pts[0][3] = last[2], last[3]
        subpath['closed'] = True
    if force_close and len(pts) > 1:
        subpath['closed'] = True
    return subpath


def _anchor(space, p):
    x, y = space.point(p.x, p.y)
    return [x, y, x, y, x, y]


def drawing_subpaths(items, space, close_last=False):
    """PyMuPDF drawing items -> list of {'points': [[x, y, lx, ly, rx, ry], ...], 'closed': bool}."""
    out, current = [], None

    def start(p):
        nonlocal current
        if current is None or not _same(current['points'][-1][:2], space.point(p.x, p.y)):
            if current:
                out.append(_finish(current))
            current = {'points': [_anchor(space, p)], 'closed': False}

    for item in items:
        op = item[0]
        if op == 'l':
            start(item[1])
            current['points'].append(_anchor(space, item[2]))
        elif op == 'c':
            start(item[1])
            current['points'][-1][4:6] = space.point(item[2].x, item[2].y)
            end = _anchor(space, item[4])
            end[2:4] = space.point(item[3].x, item[3].y)
            current['points'].append(end)
        elif op in ('re', 'qu'):
            if current:
                out.append(_finish(current))
                current = None
            if op == 're':
                rect = item[1]
                corners = [rect.tl, rect.tr, rect.br, rect.bl]
                if len(item) > 2 and item[2] == -1:
                    corners.reverse()
            else:
                quad = item[1]
                corners = [quad.ul, quad.ur, quad.lr, quad.ll]
            out.append({'points': [_anchor(space, p) for p in corners], 'closed': True})
    if current:
        out.append(_finish(current, force_close=close_last))
    elif close_last and out:
        out[-1]['closed'] = True
    return [s for s in out if len(s['points']) > 1]


def _path_items(drawing, space):
    subpaths = drawing_subpaths(drawing.get('items', []), space, bool(drawing.get('closePath')))
    if not subpaths:
        return []
    kind = drawing.get('type', '')
    fill = _color(drawing.get('fill')) if 'f' in kind else None
    stroke = _color(drawing.get('color')) if 's' in kind else None
    if fill is None and stroke is None:
        return []
    dash, phase = _parse_dashes(drawing.get('dashes') or '[] 0')
    caps = drawing.get('lineCap') or (0,)
    base = {'kind': 'path', 'seqno': drawing.get('seqno', 0), 'subpaths': subpaths,
            'even_odd': bool(drawing.get('even_odd')),
            'stroke_width': _r(drawing.get('width') or 0), 'cap': int(max(caps) if caps else 0),
            'join': int(drawing.get('lineJoin') or 0), 'dash': dash, 'dash_phase': phase}
    fill_op = drawing.get('fill_opacity')
    stroke_op = drawing.get('stroke_opacity')
    fill_op = 1.0 if fill_op is None else float(fill_op)
    stroke_op = 1.0 if stroke_op is None else float(stroke_op)
    if fill and stroke and abs(fill_op - stroke_op) > _EPS:
        # One Illustrator object has one opacity: split instead of approximating.
        return [dict(base, fill=fill, stroke=None, opacity=_r(fill_op)),
                dict(base, fill=None, stroke=stroke, opacity=_r(stroke_op))]
    return [dict(base, fill=fill, stroke=stroke, opacity=_r(fill_op if fill else stroke_op))]


# --------------------------------------------------------------------------------------------- glyph outlines
class _CubicPen:
    """fontTools pen collecting cubic contours in font units (quadratics converted exactly)."""

    def __new__(cls):
        from fontTools.pens.basePen import BasePen

        class Pen(BasePen):
            def __init__(self):
                super().__init__(None)
                self.contours, self.current = [], None

            def _moveTo(self, p):
                self.current = [[p, p, p]]  # anchor, left handle, right handle

            def _lineTo(self, p):
                self.current.append([p, p, p])

            def _curveToOne(self, c1, c2, p):
                self.current[-1][2] = c1
                self.current.append([p, c2, p])

            def _qCurveToOne(self, c, p):
                p0 = self.current[-1][0]
                c1 = (p0[0] + 2 / 3 * (c[0] - p0[0]), p0[1] + 2 / 3 * (c[1] - p0[1]))
                c2 = (p[0] + 2 / 3 * (c[0] - p[0]), p[1] + 2 / 3 * (c[1] - p[1]))
                self._curveToOne(c1, c2, p)

            def _closePath(self):
                self._end(True)

            def _endPath(self):
                self._end(False)

            def _end(self, closed):
                pts = self.current or []
                if len(pts) > 1 and abs(pts[0][0][0] - pts[-1][0][0]) < _EPS and abs(pts[0][0][1] - pts[-1][0][1]) < _EPS:
                    last = pts.pop()
                    pts[0][1] = last[1]
                if len(pts) > 1:
                    self.contours.append((pts, closed))
                self.current = None

        return Pen()


class _EmbeddedFont:
    def __init__(self, buffer, ext):
        self.draw = None
        self.postscript_name = None
        self.scale = 1 / 1000
        self.has = lambda gid: True
        data = io.BytesIO(buffer)
        if ext in ('ttf', 'otf'):
            from fontTools.ttLib import TTFont
            font = TTFont(data, lazy=True)
            try:
                self.postscript_name = font['name'].getDebugName(6)
            except Exception:
                self.postscript_name = None
            order = font.getGlyphOrder()
            glyphs = font.getGlyphSet()
            self.scale = 1 / font['head'].unitsPerEm
            self.count = len(order)

            def draw(gid, pen):
                glyphs[order[gid]].draw(pen)
            self.draw = draw
        elif ext in ('cff', 'cid'):
            from fontTools.cffLib import CFFFontSet
            from fontTools.ttLib import TTFont
            cff = CFFFontSet()
            cff.decompile(data, TTFont())
            top = cff.topDictIndex[0]
            self.postscript_name = cff.fontNames[0] if cff.fontNames else None
            charset = top.charset
            strings = top.CharStrings
            self.scale = float(getattr(top, 'FontMatrix', [0.001])[0])
            if hasattr(top, 'ROS'):
                # CID-keyed: PDF glyph ids are CIDs; subsets keep only a few, named cidNNNNN.
                names = {int(n[3:]): n for n in charset if n.startswith('cid')}
                names[0] = charset[0]
                self.count = max(names) + 1

                def draw(gid, pen):
                    strings[names[gid]].draw(pen)
                self.has = names.__contains__
            else:
                self.count = len(charset)

                def draw(gid, pen):
                    strings[charset[gid]].draw(pen)
            self.draw = draw

    def contours(self, gid):
        if self.draw is None or gid < 0 or gid >= self.count or not self.has(gid):
            return None
        pen = _CubicPen()
        self.draw(gid, pen)
        return pen.contours


class GlyphOutliner:
    """Turns PDF text runs into vector outlines using the PDF's own embedded font programs."""

    def __init__(self, document, page):
        self.fonts = {}
        try:
            import fontTools  # noqa: F401
            self.available = True
        except ImportError:
            self.available = False
            return
        by_name = {}
        for entry in page.get_fonts(full=True):
            by_name.setdefault(_strip_subset(entry[3]), []).append(entry[0])
        self.document, self.by_name, self.cache = document, by_name, {}

    def _font(self, xref):
        if xref not in self.cache:
            font = None
            try:
                _, ext, _, buffer = self.document.extract_font(xref)
                if buffer:
                    font = _EmbeddedFont(buffer, ext)
                    if font.draw is None:
                        font = None
            except Exception:
                font = None
            self.cache[xref] = font
        return self.cache[xref]

    def postscript_names(self, pdf_font):
        if not self.available:
            return []
        names = []
        for xref in self.by_name.get(_strip_subset(pdf_font), []):
            font = self._font(xref)
            if font and font.postscript_name and _strip_subset(font.postscript_name) not in names:
                names.append(_strip_subset(font.postscript_name))
        return names

    def outline(self, run, affines):
        """Return (subpaths, reason). affines[i] maps em units (y up) of char i to Illustrator coordinates."""
        if not self.available:
            return None, 'fonttools_not_installed'
        candidates = [f for f in (self._font(x) for x in self.by_name.get(_strip_subset(run['font']), [])) if f]
        if not candidates:
            return None, 'font_not_embedded_or_unsupported'
        if len(candidates) > 1:
            # Several subsets share the name; only accept the outline if exactly one holds every glyph.
            candidates = [c for c in candidates if all(0 <= ch[1] < c.count and c.has(ch[1]) for ch in run['chars'])]
            if len(candidates) != 1:
                return None, 'ambiguous_font_subset'
        font = candidates[0]
        subpaths = []
        for (_, gid, _origin, _bbox), (a, b, c, d, e, f) in zip(run['chars'], affines):
            contours = font.contours(gid)
            if contours is None:
                return None, 'glyph_missing'
            k = font.scale

            def map_point(g):
                u, v = g[0] * k, g[1] * k
                return [_r(a * u + c * v + e), _r(b * u + d * v + f)]

            for pts, closed in contours:
                subpaths.append({'points': [map_point(p) + map_point(left) + map_point(right) for p, left, right in pts],
                                 'closed': closed})
        return subpaths, None


# --------------------------------------------------------------------------------------------- text
def _project(point, direction):
    return point[0] * direction[0] + point[1] * direction[1]


def _far_edge(bbox, direction):
    return max(_project(corner, direction) for corner in
               ((bbox[0], bbox[1]), (bbox[2], bbox[1]), (bbox[0], bbox[3]), (bbox[2], bbox[3])))


def _split_runs(span):
    """Split a texttrace span into runs on one straight baseline; restore word spaces the PDF only positioned."""
    direction = span['dir']
    normal = (-direction[1], direction[0])
    size = span['size'] or 1
    space_width = span.get('spacewidth') or size * 0.25
    runs, current, previous = [], None, None
    for char in span['chars']:
        if previous is not None:
            along = _project(char[2], direction) - _project(previous[2], direction)
            across = _project(char[2], normal) - _project(previous[2], normal)
            if abs(across) > size * 0.1 or along < -size * 0.05:
                runs.append(current)
                current = None
        if current is None:
            current = {'chars': [], 'text': ''}
        elif char[0] != 32 and previous[0] != 32:
            gap = _project(char[2], direction) - _far_edge(previous[3], direction)
            if gap > space_width * 0.5:
                current['text'] += ' '
        current['chars'].append(char)
        current['text'] += chr(char[0]) if char[0] > 0 else '\ufffd'
        previous = char
    if current:
        runs.append(current)
    return runs


def _affine(to_ai, origin, u, v):
    """Affine (a, b, c, d, e, f) through three mapped points: origin, origin+u, origin+v."""
    p0, pu, pv = to_ai(*origin), to_ai(origin[0] + u[0], origin[1] + u[1]), to_ai(origin[0] + v[0], origin[1] + v[1])
    return (pu[0] - p0[0], pu[1] - p0[1], pv[0] - p0[0], pv[1] - p0[1], p0[0], p0[1])


def _svg_matrix(text):
    if not text:
        return (1, 0, 0, 1, 0, 0)
    import re
    found = re.fullmatch(r'\s*matrix\(([^)]*)\)\s*', text)
    if not found:
        raise ValueError('unsupported SVG transform %r' % text)
    values = [float(x) for x in re.split(r'[\s,]+', found.group(1).strip())]
    return tuple(values)


def _mul(m, n):
    """m applied after n (both SVG/PDF style a b c d e f)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + c * B, b * A + d * B, a * C + c * D, b * C + d * D, a * E + c * F + e, b * E + d * F + f)


def svg_text_runs(page):
    """Exact text geometry from MuPDF's SVG device: one entry per visible text span, in paint order.

    texttrace only reports a direction and a size, so skewed, condensed or mirrored type would be placed wrongly;
    the SVG device writes the full text rendering matrix and every glyph position."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(page.get_svg_image(text_as_path=False))
    ns = '{http://www.w3.org/2000/svg}'
    runs = []

    def walk(node, matrix):
        here = _mul(matrix, _svg_matrix(node.get('transform')))
        if node.tag == ns + 'text':
            size = float(node.get('font-size', '0'))
            for tspan in node:
                xs = [float(x) for x in (tspan.get('x') or '').split()]
                runs.append({'matrix': here, 'size': size, 'y': float(tspan.get('y', '0')), 'xs': xs,
                             'text': tspan.text or ''})
            return
        for child in node:
            walk(child, here)
    walk(root, (1, 0, 0, 1, 0, 0))
    return runs


def _char_geometry(span, space, svg):
    """Per char: affine from em units (y up) to Illustrator space, baseline position and stretch."""
    if svg is not None:
        m, size, y = svg['matrix'], svg['size'], svg['y']

        def to_ai(x, yy):
            return space.visual(m[0] * x + m[2] * yy + m[4], m[1] * x + m[3] * yy + m[5])
        affines = [_affine(to_ai, (x, y), (size, 0), (0, -size)) for x in svg['xs']]
        return affines, list(svg['xs']), math.hypot(m[0], m[1]) or 1, size, 'exact'
    dx, dy = span['dir']
    size = span['size']
    affines = [_affine(space.point, c[2], (size * dx, size * dy), (size * dy, -size * dx)) for c in span['chars']]
    return affines, [_project(c[2], span['dir']) for c in span['chars']], 1.0, size, 'direction_only'


def _text_items(span, space, outliner, font_map, text_mode, svg=None):
    if span.get('type') == 3:
        return [], 'invisible_text'
    if not span.get('chars'):
        return [], None
    if svg is not None and (len(svg['xs']) != len(span['chars']) or
                            svg['text'] != ''.join(chr(c[0]) if c[0] > 0 else '\ufffd' for c in span['chars'])):
        svg = None
    affines, positions, stretch, size, geometry = _char_geometry(span, space, svg)
    index = {id(c): i for i, c in enumerate(span['chars'])}
    vertical = bool(span.get('wmode'))
    items = []
    colour = _color(span.get('color'))
    mapped = font_map.get(span['font']) or font_map.get(_strip_subset(span['font']))
    mapped_name = mapped.get('illustrator_name') if isinstance(mapped, dict) else mapped
    candidates = [mapped_name] if mapped_name else []
    for name in outliner.postscript_names(span['font']) + [_postscript_guess(span['font'])]:
        if name and name not in candidates:
            candidates.append(name)
    for run in _split_runs(span):
        ids = [index[id(c)] for c in run['chars']]
        run_affines = [affines[i] for i in ids]
        first, last = ids[0], ids[-1]
        last_width = (_far_edge(span['chars'][last][3], span['dir']) - _project(span['chars'][last][2], span['dir'])) / stretch
        a, b, c, d, e, f = run_affines[0]
        live_reason = None
        if vertical:
            live_reason = 'vertical_writing_mode'
        elif '\ufffd' in run['text'] or not run['text'].strip():
            live_reason = 'unreliable_unicode'
        render = span.get('type', 0)
        corners = [[p[0] * k[0] + p[1] * k[2] + k[4], p[0] * k[1] + p[1] * k[3] + k[5]]
                   for k in run_affines for p in ((0, -0.2), (0, 0.8), (0.9, -0.2), (0.9, 0.8))]
        item = {'kind': 'text', 'seqno': span.get('seqno', 0), 'text': run['text'], 'pdf_font': span['font'],
                'font': candidates[0], 'font_candidates': [] if mapped_name else candidates, 'font_mapped': bool(mapped),
                'size': _r(size), 'origin': [_r(e), _r(f)],
                # Illustrator matrix applied to point text set at `size` (identity for plain horizontal type).
                'matrix': [_r(a / size), _r(b / size), _r(c / size), _r(d / size)],
                'advance': _r(positions[last] - positions[first] + last_width), 'geometry': geometry,
                'bounds': [_r(min(p[0] for p in corners)), _r(max(p[1] for p in corners)),
                           _r(max(p[0] for p in corners)), _r(min(p[1] for p in corners))],
                'chars': len(run['chars']), 'opacity': _r(span.get('opacity', 1) if span.get('opacity') is not None else 1),
                'fill': colour if render in (0, 2, 4, 6) else None,
                'stroke': colour if render in (1, 2, 5, 6) else None,
                'stroke_width': _r(span.get('linewidth') or 0), 'live_blocker': live_reason}
        if text_mode != 'live':
            outline, reason = (None, 'vertical_writing_mode') if vertical else \
                outliner.outline(dict(span, chars=run['chars']), run_affines)
            item['outline'] = outline
            item['outline_blocker'] = reason
        items.append(item)
    return items, None


# --------------------------------------------------------------------------------------------- images
def _image_items(document, page, space, assets, page_number, inline_blocks):
    import pymupdf
    items, problems = [], []
    order = [i for i, entry in enumerate(page.get_bboxlog()) if entry[0] == 'fill-image']
    for index, info in enumerate(page.get_image_info(xrefs=True)):
        seqno = order[index] if index < len(order) else 10 ** 6 + index
        name = 'p%03d-img%03d.png' % (page_number, index + 1)
        target = assets / name
        try:
            xref = info.get('xref', 0)
            if xref:
                pix = pymupdf.Pixmap(document, xref)
                smask = document.extract_image(xref).get('smask', 0) if not pix.alpha else 0
                if pix.colorspace and pix.colorspace.n not in (1, 3):
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                if smask:
                    pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(document, smask))
                pix.save(target)
            else:
                block = next((b for b in inline_blocks if pymupdf.Rect(b['bbox']) == pymupdf.Rect(info['bbox'])), None)
                if block is None:
                    raise ValueError('inline image bytes not found')
                pix = pymupdf.Pixmap(block['image'])
                if pix.colorspace and pix.colorspace.n not in (1, 3):
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                pix.save(target)
        except Exception as error:  # keep going; the report shows what is missing
            problems.append({'page': page_number, 'image': index + 1, 'reason': 'extract_failed: %s' % error})
            continue
        a, b, c, d, e, f = info['transform']
        # Unit square corners (y-down image space) -> page -> Illustrator space.
        corners = [space.point(e, f), space.point(a + e, b + f), space.point(a + c + e, b + d + f), space.point(c + e, d + f)]
        top_left, top_right, bottom_left = corners[0], corners[1], corners[3]
        width = math.hypot(top_right[0] - top_left[0], top_right[1] - top_left[1])
        height = math.hypot(bottom_left[0] - top_left[0], bottom_left[1] - top_left[1])
        angle = math.degrees(math.atan2(top_right[1] - top_left[1], top_right[0] - top_left[0]))
        ux = ((top_right[0] - top_left[0]), (top_right[1] - top_left[1]))
        vx = ((bottom_left[0] - top_left[0]), (bottom_left[1] - top_left[1]))
        cross = ux[0] * vx[1] - ux[1] * vx[0]
        dot = ux[0] * vx[0] + ux[1] * vx[1]
        center = [_r(sum(p[0] for p in corners) / 4), _r(sum(p[1] for p in corners) / 4)]
        items.append({'kind': 'image', 'seqno': seqno, 'file': 'assets/' + name, 'width': _r(width), 'height': _r(height),
                      'angle': _r(angle), 'mirrored': cross > 0, 'center': center,
                      'skewed': width > 0 and height > 0 and abs(dot) / (width * height) > 1e-3,
                      'pixels': [info['width'], info['height']]})
    return items, problems


# --------------------------------------------------------------------------------------------- compile
def _page_list(spec, count):
    if not spec:
        return list(range(count))
    chosen = []
    for part in str(spec).split(','):
        part = part.strip()
        if '-' in part:
            start, end = part.split('-', 1)
            chosen.extend(range(int(start) - 1, int(end)))
        elif part:
            chosen.append(int(part) - 1)
    if not chosen or any(n < 0 or n >= count for n in chosen):
        raise ValueError('Page selection out of range (document has %d pages)' % count)
    return chosen


def compile_pdf(source, out, font_map=None, text_mode='auto', color_mode='auto', pages=None, embed_images=True,
                engine='illustrator-import'):
    """Compile a PDF into an Illustrator build job. Returns a summary; the full plan is written to compile-ir.json.

    engine='illustrator-import' (default) lets Illustrator's own PDF importer reproduce the graphics (clipping,
    gradients, transparency groups), places every page on its own artboard and replaces the imported text fragments
    with clean runs from the IR. engine='ir' rebuilds every object from the IR alone (no clipping/shading yet).
    """
    import pymupdf
    import shutil
    if engine not in ENGINES:
        raise ValueError('engine must be one of %s' % (ENGINES,))
    if text_mode not in TEXT_MODES or (text_mode == 'keep' and engine != 'illustrator-import'):
        raise ValueError('text_mode must be one of %s (keep needs the illustrator-import engine)' % (TEXT_MODES,))
    if color_mode not in COLOR_MODES:
        raise ValueError('color_mode must be one of %s' % (COLOR_MODES,))
    font_map = font_map or {}
    source = Path(source).expanduser().resolve()
    out = Path(out).expanduser().resolve()
    if out == source or out in source.parents or source.parent == out:
        raise ValueError('Output directory must be a new directory that does not contain the source')
    source_hash = digest(source)
    out.mkdir(parents=True, exist_ok=False)
    assets = out / 'assets'
    assets.mkdir()
    ir_pages, problems, unsupported, colour_spaces = [], [], {}, {'rgb': 0, 'cmyk': 0, 'gray': 0}
    offset = 0.0
    with pymupdf.open(source) as document:
        if not document.is_pdf or document.needs_pass:
            raise ValueError('An unlocked PDF is required')
        for n in _page_list(pages, document.page_count):
            page = document[n]
            space = _Space(page, offset)
            width, height = page.rect.width, page.rect.height
            items = []
            if engine == 'ir':
                for drawing in page.get_drawings():
                    items.extend(_path_items(drawing, space))
            outliner = GlyphOutliner(document, page)
            spans = page.get_texttrace()
            try:
                svg_runs = svg_text_runs(page)
            except Exception:
                svg_runs = []
            visible = [sp for sp in spans if sp.get('type') != 3 and sp.get('chars')]
            exact = {id(sp): run for sp, run in zip(visible, svg_runs)} if len(visible) == len(svg_runs) else {}
            for span in spans:
                found, skipped = _text_items(span, space, outliner, font_map, text_mode, exact.get(id(span)))
                items.extend(found)
                if skipped:
                    unsupported[skipped] = unsupported.get(skipped, 0) + 1
            inline = [] if engine != 'ir' else [b for b in page.get_text('dict', flags=pymupdf.TEXT_PRESERVE_IMAGES)['blocks'] if b.get('type') == 1]
            if engine == 'ir':
                found, image_problems = _image_items(document, page, space, assets, n + 1, inline)
                items.extend(found)
                problems.extend(image_problems)
            for kind, _ in page.get_bboxlog():
                if engine == 'ir' and kind not in _HANDLED_LOG:
                    unsupported[kind] = unsupported.get(kind, 0) + 1
            items.sort(key=lambda item: item['seqno'])
            for item in items:
                for key in ('fill', 'stroke'):
                    if item.get(key):
                        colour_spaces[item[key]['space']] += 1
            ir_pages.append({'page': n + 1, 'width': _r(width), 'height': _r(height),
                             'artboard': [_r(offset), 0.0, _r(offset + width), _r(-height)], 'items': items})
            offset += width + PAGE_GAP_PT
    if color_mode == 'auto':
        color_mode = 'cmyk' if colour_spaces['cmyk'] > colour_spaces['rgb'] else 'rgb'
    summary = summarize(ir_pages, text_mode)
    source_copy = None
    if engine == 'illustrator-import':
        source_copy = 'source.pdf'
        shutil.copy2(source, out / source_copy)
    plan = {'schema': SCHEMA, 'engine': engine, 'source': str(source), 'source_copy': source_copy,
            'source_sha256': source_hash, 'source_name': source.name,
            'text_mode': text_mode, 'color_mode': color_mode, 'embed_images': bool(embed_images),
            'font_map': font_map, 'proof_scale': 200, 'pages': ir_pages, 'summary': summary,
            'unsupported_pdf_operations': unsupported, 'problems': problems,
            'output': {'ai': source.stem + '.ai', 'build_report': 'build-report.json',
                       'verification': 'native-verification.json', 'proof_prefix': 'proof-artboard-'},
            'status': 'requires_illustrator_execution',
            'limitations': [
                'illustrator-import engine: graphics fidelity is whatever Illustrator\'s PDF importer produces '
                '(it keeps clipping masks, gradients and transparency, but its object structure is not cleaned up).',
                'ir engine: Clipping paths, shadings/gradients, soft masks, blend modes and image masks are not reproduced yet; '
                'they are counted in unsupported_pdf_operations and show up in the visual comparison.',
                'Live text uses the exact PostScript font name (or the font map); it is never silently substituted. '
                'A run whose font is missing in Illustrator falls back to embedded-font outlines in auto mode.',
                'Each PDF text run becomes one point-text object; paragraphs and reflowing stories are not rebuilt.',
                'Colours keep their PDF space (RGB/CMYK/Gray); Illustrator converts to the document mode.',
            ]}
    atomic_json(out / 'compile-ir.json', plan)
    preview_info = None
    if engine == 'ir':
        preview = preview_ir(plan, out / 'ir-preview.pdf')
        selected = pymupdf.open()
        with pymupdf.open(source) as document:
            for page in ir_pages:
                selected.insert_pdf(document, from_page=page['page'] - 1, to_page=page['page'] - 1)
        reference = out / 'source-pages.pdf'
        selected.save(reference)
        selected.close()
        preview_info = {'pdf': preview, 'pixels_over_32_per_page': compare_pdfs(reference, preview),
                        'note': 'IR redrawn by PyMuPDF (outlined text only); not Illustrator evidence'}
        reference.unlink()
    (out / 'build-illustrator.jsx').write_text(build_script(), encoding='utf-8')
    (out / 'verify-illustrator.jsx').write_text(verify_script(), encoding='utf-8')
    if digest(source) != source_hash:
        raise RuntimeError('Source changed during compilation')
    return {'job': str(out), 'ir': str(out / 'compile-ir.json'), 'script': str(out / 'build-illustrator.jsx'),
            'verify_script': str(out / 'verify-illustrator.jsx'), 'expected_ai': str(out / plan['output']['ai']),
            'pages': len(ir_pages), 'color_mode': color_mode, 'summary': summary,
            'unsupported_pdf_operations': unsupported, 'problems': problems,
            'engine': engine, 'front_end_preview': preview_info,
            'status': plan['status']}


def summarize(pages, text_mode='auto'):
    s = {'paths': 0, 'compound_paths': 0, 'images': 0, 'text_runs': 0, 'text_outline_ready': 0,
         'text_live_blocked': 0, 'text_unresolvable_without_font': 0}
    for page in pages:
        for item in page['items']:
            if item['kind'] == 'path':
                s['paths'] += 1
                s['compound_paths'] += len(item['subpaths']) > 1
            elif item['kind'] == 'image':
                s['images'] += 1
            else:
                s['text_runs'] += 1
                ready = bool(item.get('outline'))
                s['text_outline_ready'] += ready
                s['text_live_blocked'] += bool(item['live_blocker'])
                if not ready and (item['live_blocker'] or text_mode == 'outline'):
                    s['text_unresolvable_without_font'] += 1
    return s


# --------------------------------------------------------------------------------------------- ExtendScript
_JSX_COMMON = r'''
var ROOT=File($.fileName).parent;
function readText(f){f.encoding="UTF-8";if(!f.open("r"))throw new Error("Cannot read "+f.fsName);var s=f.read();f.close();return s;}
function writeText(name,s){var f=File(ROOT.fsName+"/"+name);f.encoding="UTF-8";f.open("w");f.write(s);f.close();}
function q(s){return '"'+String(s).replace(/\\/g,"\\\\").replace(/"/g,'\\"').replace(/[\r\n\t]/g," ")+'"';}
function toJSON(v){
 if(v===null||v===undefined)return "null";
 if(typeof v==="number")return isFinite(v)?String(v):"null";
 if(typeof v==="boolean")return v?"true":"false";
 if(typeof v==="string")return q(v);
 if(v instanceof Array){var a=[];for(var i=0;i<v.length;i++)a.push(toJSON(v[i]));return "["+a.join(",")+"]";}
 var o=[];for(var k in v)if(v.hasOwnProperty(k))o.push(q(k)+":"+toJSON(v[k]));return "{"+o.join(",")+"}";
}
var IR=eval("("+readText(File(ROOT.fsName+"/compile-ir.json"))+")");
'''

_BUILD_BODY = r'''
var target=File(ROOT.fsName+"/"+IR.output.ai);
if(target.exists)throw new Error("Refusing overwrite: "+target.fsName);
var previousLevel=app.userInteractionLevel;app.userInteractionLevel=UserInteractionLevel.DONTDISPLAYALERTS;
var doc=null,report={schema:"ali-pdf-ai-build/1",pages:[],missing_fonts:{},live_text:0,outline_text:0,unresolved_text:[],
 paths:0,compound_paths:0,images:0,image_approximations:0,errors:[],engine:IR.engine,
 imported_items:0,imported_text_replaced:0,imported_text_fragments_removed:0,imported_text_kept:0};
function progress(m){writeText("build-progress.txt",m);}
function color(c){
 if(!c)return null;var x;
 if(c.space=="cmyk"){x=new CMYKColor();x.cyan=c.v[0]*100;x.magenta=c.v[1]*100;x.yellow=c.v[2]*100;x.black=c.v[3]*100;}
 else if(c.space=="gray"){x=new GrayColor();x.gray=(1-c.v[0])*100;}
 else{x=new RGBColor();x.red=Math.round(c.v[0]*255);x.green=Math.round(c.v[1]*255);x.blue=Math.round(c.v[2]*255);}
 return x;
}
var fonts={};
function font(name){if(fonts.hasOwnProperty(name))return fonts[name];var f=null;try{f=app.textFonts.getByName(name);}catch(e){f=null;}fonts[name]=f;return f;}
var CAPS=[StrokeCap.BUTTENDCAP,StrokeCap.ROUNDENDCAP,StrokeCap.PROJECTINGENDCAP];
var JOINS=[StrokeJoin.MITERENDJOIN,StrokeJoin.ROUNDENDJOIN,StrokeJoin.BEVELENDJOIN];
function paint(p,s){
 var f=color(s.fill),k=color(s.stroke);
 if(f){p.filled=true;p.fillColor=f;}else p.filled=false;
 if(k&&s.stroke_width>0){p.stroked=true;p.strokeColor=k;p.strokeWidth=s.stroke_width;p.strokeCap=CAPS[s.cap]||CAPS[0];p.strokeJoin=JOINS[s.join]||JOINS[0];
  if(s.dash&&s.dash.length){p.strokeDashes=s.dash;try{p.strokeDashOffset=s.dash_phase;}catch(e){}}}
 else if(k){p.stroked=true;p.strokeColor=k;p.strokeWidth=0.25;}
 else p.stroked=false;
 if(s.even_odd)p.evenodd=true;
}
function addPath(container,sp){
 var p=container.pathItems.add(),pts=sp.points,anchors=[];
 for(var i=0;i<pts.length;i++)anchors.push([pts[i][0],pts[i][1]]);
 p.setEntirePath(anchors);
 for(var i=0;i<pts.length;i++){var pp=p.pathPoints[i];pp.leftDirection=[pts[i][2],pts[i][3]];pp.rightDirection=[pts[i][4],pts[i][5]];}
 p.closed=sp.closed;return p;
}
function addShape(layer,item,style,name,counted){
 var obj;
 if(item.subpaths.length==1){obj=addPath(layer,item.subpaths[0]);paint(obj,style);if(counted)report.paths++;}
 else{obj=layer.compoundPathItems.add();for(var i=0;i<item.subpaths.length;i++){var p=addPath(obj,item.subpaths[i]);paint(p,style);}if(counted)report.compound_paths++;}
 if(style.opacity!==undefined&&style.opacity<1)obj.opacity=style.opacity*100;
 obj.name=name;return obj;
}
function addImage(layer,item,n){
 var placed=layer.placedItems.add();placed.file=File(ROOT.fsName+"/"+item.file);
 placed.width=item.width;placed.height=item.height;
 if(item.mirrored)placed.resize(-100,100);
 if(Math.abs(item.angle)>0.001)placed.rotate(item.angle);
 var b=placed.geometricBounds;placed.translate(item.center[0]-(b[0]+b[2])/2,item.center[1]-(b[1]+b[3])/2);
 if(item.skewed)report.image_approximations++;
 placed.name="image "+n;
 if(IR.embed_images)placed.embed();
 report.images++;
}
function addLiveText(layer,item,f){
 var t=layer.textFrames.pointText([item.origin[0],item.origin[1]]);
 t.contents=item.text;
 var ca=t.textRange.characterAttributes;ca.textFont=f;ca.size=item.size;ca.tracking=0;ca.horizontalScale=100;
 var fill=color(item.fill),stroke=color(item.stroke);
 if(fill)ca.fillColor=fill;else ca.fillColor=new NoColor();
 if(stroke){ca.strokeColor=stroke;ca.strokeWeight=item.stroke_width||0.25;}else ca.strokeColor=new NoColor();
 if(item.chars>1&&item.advance>0){var w=t.width;var tr=Math.round((item.advance-w)/(item.text.length-1)/item.size*1000);
  if(Math.abs(item.advance-w)>0.5)ca.tracking=Math.max(-300,Math.min(600,tr));}
 var m=item.matrix;
 if(Math.abs(m[0]-1)>0.0005||Math.abs(m[1])>0.0005||Math.abs(m[2])>0.0005||Math.abs(m[3]-1)>0.0005){
  var mx=app.getIdentityMatrix();mx.mValueA=m[0];mx.mValueB=m[1];mx.mValueC=m[2];mx.mValueD=m[3];mx.mValueTX=0;mx.mValueTY=0;
  t.transform(mx,true,false,false,false,false,Transformation.DOCUMENTORIGIN);
 }
 var a=t.anchor;if(Math.abs(a[0]-item.origin[0])>0.001||Math.abs(a[1]-item.origin[1])>0.001)t.translate(item.origin[0]-a[0],item.origin[1]-a[1]);
 if(item.opacity<1)t.opacity=item.opacity*100;
 t.name=item.text.substr(0,60);t.note="ali-live";report.live_text++;return t;
}
function addOutlineText(layer,item){
 var g=layer.groupItems.add();g.name="outlined: "+item.text.substr(0,50);
 var shape={subpaths:item.outline};
 var style={fill:item.fill,stroke:item.stroke,stroke_width:item.stroke_width,cap:0,join:0,even_odd:false};
 if(item.outline.length)addShape(g,shape,style,"glyphs",false);
 if(item.opacity<1)g.opacity=item.opacity*100;
 g.note="ali-outline";report.outline_text++;return g;
}
function placeText(layer,item,pageNumber){
 var f=null;
 if(IR.text_mode!="outline"&&!item.live_blocker){f=font(item.font);for(var c=1;!f&&item.font_candidates&&c<item.font_candidates.length;c++)f=font(item.font_candidates[c]);if(!f)report.missing_fonts[item.font]=true;}
 if(f)return addLiveText(layer,item,f);
 if(item.outline&&IR.text_mode!="live")return addOutlineText(layer,item);
 report.unresolved_text.push({page:pageNumber,text:item.text,font:item.font,reason:item.live_blocker||item.outline_blocker||"font_missing_in_illustrator"});
 return null;
}
function overlap(a,b){var w=Math.min(a[2],b[2])-Math.max(a[0],b[0]),h=Math.min(a[1],b[1])-Math.max(a[3],b[3]);return w>0&&h>0?w*h:0;}
function area(a){return Math.max(0.0001,(a[2]-a[0])*(a[1]-a[3]));}
function unlockAll(layer){layer.locked=false;layer.visible=true;for(var i=0;i<layer.layers.length;i++)unlockAll(layer.layers[i]);}
function copyLayer(src,dstParent,dx,dy){
 var dst=dstParent.layers.add();dst.name=src.name;
 for(var i=src.layers.length-1;i>=0;i--)copyLayer(src.layers[i],dst,dx,dy);
 for(var i=src.pageItems.length-1;i>=0;i--){var it=src.pageItems[i];var wasLocked=it.locked;it.locked=false;var d=it.duplicate(dst,ElementPlacement.PLACEATBEGINNING);d.translate(dx,dy);d.locked=wasLocked;report.imported_items++;}
 return dst;
}
function importPage(page,layer){
 var o=app.preferences.PDFFileOptions;o.pageToOpen=page.page;o.pDFCropToBox=PDFBoxType.PDFCROPBOX;
 var src=app.open(File(ROOT.fsName+"/"+IR.source_copy));
 try{
  app.coordinateSystem=CoordinateSystem.DOCUMENTCOORDINATESYSTEM;
  var r=src.artboards[0].artboardRect;var dx=page.artboard[0]-r[0],dy=page.artboard[1]-r[1];
  for(var i=0;i<src.layers.length;i++)unlockAll(src.layers[i]);
  for(var i=src.layers.length-1;i>=0;i--)copyLayer(src.layers[i],layer,dx,dy);
 }finally{src.close(SaveOptions.DONOTSAVECHANGES);app.activeDocument=doc;app.coordinateSystem=CoordinateSystem.DOCUMENTCOORDINATESYSTEM;}
}
function replaceImportedText(page,layer){
 var frames=[];for(var i=0;i<doc.textFrames.length;i++){var t=doc.textFrames[i];if(t.note.indexOf("ali-")!=0)frames.push({frame:t,bounds:t.visibleBounds,runs:[],hits:0});}
 var runs=[];for(var i=0;i<page.items.length;i++)if(page.items[i].kind=="text")runs.push(page.items[i]);
 var loose=[];
 for(var i=0;i<runs.length;i++){
  var best=null,score=0;
  for(var j=0;j<frames.length;j++){var o=overlap(runs[i].bounds,frames[j].bounds);if(o>0)frames[j].hits++;if(o>score){score=o;best=frames[j];}}
  if(best&&score>0.3*area(runs[i].bounds))best.runs.push(runs[i]);else loose.push(runs[i]);
 }
 for(var j=0;j<frames.length;j++){
  var fr=frames[j];
  if(fr.runs.length){
   for(var k=0;k<fr.runs.length;k++){var obj=placeText(layer,fr.runs[k],page.page);if(obj)obj.move(fr.frame,ElementPlacement.PLACEBEFORE);}
   fr.frame.remove();report.imported_text_replaced++;
  }else if(fr.hits){fr.frame.remove();report.imported_text_fragments_removed++;}
  else{fr.frame.note="ali-kept";report.imported_text_kept++;}
 }
 for(var k=0;k<loose.length;k++)placeText(layer,loose[k],page.page);
}
try{
 var first=IR.pages[0];
 var mode=IR.color_mode=="cmyk"?DocumentColorSpace.CMYK:DocumentColorSpace.RGB;
 doc=app.documents.add(mode,first.width,first.height);
 app.coordinateSystem=CoordinateSystem.DOCUMENTCOORDINATESYSTEM;
 doc.artboards[0].artboardRect=first.artboard;doc.artboards[0].name="Page "+first.page;
 for(var p=1;p<IR.pages.length;p++){var ab=doc.artboards.add(IR.pages[p].artboard);ab.name="Page "+IR.pages[p].page;}
 var defaultLayer=doc.layers[0];
 for(var p=0;p<IR.pages.length;p++){
  var page=IR.pages[p];progress("page "+page.page);
  var layer=p==0?defaultLayer:doc.layers.add();layer.name="Page "+page.page;
  var start={paths:report.paths,compound:report.compound_paths,images:report.images,live:report.live_text,outline:report.outline_text,unresolved:report.unresolved_text.length};
  if(IR.engine=="illustrator-import"){
   try{importPage(page,layer);if(IR.text_mode!="keep")replaceImportedText(page,layer);}
   catch(e){report.errors.push({page:page.page,error:String(e),line:e.line});}
  }
  else for(var i=0;i<page.items.length;i++){
   var item=page.items[i];
   if(i%200==0)progress("page "+page.page+" item "+i+" / "+page.items.length);
   try{
    if(item.kind=="path")addShape(layer,item,item,"path "+item.seqno,true);
    else if(item.kind=="image")addImage(layer,item,i);
    else{
     placeText(layer,item,page.page);
    }
   }catch(e){report.errors.push({page:page.page,seqno:item.seqno,kind:item.kind,error:String(e)});}
  }
  report.pages.push({page:page.page,items:page.items.length,paths:report.paths-start.paths,compound_paths:report.compound_paths-start.compound,
   images:report.images-start.images,live_text:report.live_text-start.live,outline_text:report.outline_text-start.outline,
   unresolved_text:report.unresolved_text.length-start.unresolved});
 }
 if(IR.text_mode=="live"){var miss=[];for(var k in report.missing_fonts)miss.push(k);if(miss.length)report.errors.push({error:"live mode: fonts missing in Illustrator: "+miss.join(", ")});}
 progress("saving");
 var opts=new IllustratorSaveOptions();opts.pdfCompatible=true;opts.compressed=true;opts.embedLinkedFiles=IR.embed_images;
 doc.saveAs(target,opts);
 report.saved=target.exists;report.ai=target.fsName;
 var missing=[];for(var k in report.missing_fonts)missing.push(k);report.missing_fonts=missing;
 report.complete=report.saved&&report.errors.length==0&&report.unresolved_text.length==0;
 writeText(IR.output.build_report,toJSON(report));
 progress("done");
}catch(e){report.errors.push({fatal:String(e),line:e.line});var m=[];for(var k in report.missing_fonts)m.push(k);report.missing_fonts=m;report.complete=false;writeText(IR.output.build_report,toJSON(report));throw e;}
finally{if(doc)doc.close(SaveOptions.DONOTSAVECHANGES);app.userInteractionLevel=previousLevel;}
'''

_VERIFY_BODY = r'''
var target=File(ROOT.fsName+"/"+IR.output.ai);
if(!target.exists)throw new Error("Missing "+target.fsName);
var previousLevel=app.userInteractionLevel;app.userInteractionLevel=UserInteractionLevel.DONTDISPLAYALERTS;
var doc=null,out={schema:"ali-pdf-ai-reopen/1",saved_and_reopened:false,artboards:[],proofs:[]};
try{
 doc=app.open(target);out.saved_and_reopened=true;
 out.document_color_space=String(doc.documentColorSpace);
 out.layers=doc.layers.length;out.path_items=doc.pathItems.length;out.compound_path_items=doc.compoundPathItems.length;
 out.text_frames=doc.textFrames.length;out.raster_items=doc.rasterItems.length;out.placed_items=doc.placedItems.length;
 out.group_items=doc.groupItems.length;
 var texts=[];for(var i=0;i<doc.textFrames.length;i++)texts.push(doc.textFrames[i].contents);out.text_contents=texts;
 for(var i=0;i<doc.artboards.length;i++){var ab=doc.artboards[i];out.artboards.push({name:ab.name,rect:ab.artboardRect});}
 var opt=new ExportOptionsPNG24();opt.artBoardClipping=true;opt.transparency=false;opt.antiAliasing=true;
 opt.horizontalScale=IR.proof_scale||200;opt.verticalScale=IR.proof_scale||200;
 for(var i=0;i<doc.artboards.length;i++){
  doc.artboards.setActiveArtboardIndex(i);
  var name=IR.output.proof_prefix+(i+1)+".png";
  doc.exportFile(File(ROOT.fsName+"/"+name),ExportType.PNG24,opt);out.proofs.push(name);
 }
}finally{if(doc)doc.close(SaveOptions.DONOTSAVECHANGES);app.userInteractionLevel=previousLevel;writeText(IR.output.verification,toJSON(out));}
'''


def build_script():
    return '#target illustrator\n// Generated by OpenLayer Studio pdf-ai. Reads compile-ir.json beside this file.\n(function(){' + \
        _JSX_COMMON + _BUILD_BODY + '})();\n'


def verify_script():
    return '#target illustrator\n// Reopens the compiled .ai, counts native objects and exports one proof PNG per artboard.\n(function(){' + \
        _JSX_COMMON + _VERIFY_BODY + '})();\n'


# --------------------------------------------------------------------------------------------- verification
def verify_job(job, scale=2.0, tolerance=0.02):
    """Combine Illustrator's reopen evidence with a pixel comparison of each proof against the source PDF."""
    import pymupdf
    from PIL import Image, ImageChops, ImageStat
    job = Path(job).expanduser().resolve()
    plan = json.loads((job / 'compile-ir.json').read_text(encoding='utf-8'))
    result = {'schema': 'ali-pdf-ai-verification/1', 'job': str(job), 'source_sha256_unchanged': None,
              'build_report': None, 'reopen': None, 'pages': [], 'status': 'not_assessed'}
    source = Path(plan['source'])
    if source.exists():
        result['source_sha256_unchanged'] = digest(source) == plan['source_sha256']
    elif plan.get('source_copy') and (job / plan['source_copy']).exists():
        source = job / plan['source_copy']
        result['source_copy_sha256_matches'] = digest(source) == plan['source_sha256']
    build = job / plan['output']['build_report']
    reopen = job / plan['output']['verification']
    if build.exists():
        result['build_report'] = json.loads(build.read_text(encoding='utf-8'))
    if reopen.exists():
        result['reopen'] = json.loads(reopen.read_text(encoding='utf-8'))
    if not (result['build_report'] and result['reopen'] and result['reopen'].get('saved_and_reopened')):
        result['status'] = 'requires_illustrator_execution'
        atomic_json(job / 'verification.json', result)
        return result
    worst = 0.0
    with pymupdf.open(source) as document:
        for index, page in enumerate(plan['pages']):
            proof = job / (plan['output']['proof_prefix'] + str(index + 1) + '.png')
            row = {'page': page['page'], 'proof': proof.name}
            if not proof.exists():
                row['status'] = 'missing_proof'
                result['pages'].append(row)
                worst = 1.0
                continue
            pix = document[page['page'] - 1].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            reference = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            candidate = Image.open(proof).convert('RGB')
            if candidate.size != reference.size:
                candidate = candidate.resize(reference.size, Image.LANCZOS)
            diff = ImageChops.difference(reference, candidate).convert('L')
            mean = ImageStat.Stat(diff).mean[0] / 255
            changed = sum(diff.histogram()[33:]) / (diff.width * diff.height)
            row.update({'mean_abs_diff': round(mean, 5), 'pixels_over_32': round(changed, 5)})
            row['status'] = 'pass' if changed <= tolerance else 'review'
            diff.point(lambda v: 255 if v > 32 else 0).save(job / ('diff-artboard-%d.png' % (index + 1)))
            worst = max(worst, changed)
            result['pages'].append(row)
    report = result['build_report']
    complete = report.get('complete') and not report.get('errors')
    result['status'] = 'candidate_pass' if complete and worst <= tolerance else 'review_required'
    result['note'] = ('Pixel comparison and reopen evidence only. A reviewer must still check fonts, colour and '
                      'object structure before the .ai is accepted.')
    atomic_json(job / 'verification.json', result)
    return result


# --------------------------------------------------------------------------------------------- IR preview
def preview_ir(plan, target, live_text_fallback=False):
    """Draw the IR back into a PDF with PyMuPDF. This checks the front end (geometry, outlines, images, order)
    without Illustrator; it is not evidence that the .ai is correct."""
    import pymupdf

    def colour(c):
        if not c:
            return None
        v = c['v']
        if c['space'] == 'gray':
            return (v[0],) * 3
        if c['space'] == 'cmyk':
            return tuple((1 - min(1, x + v[3])) for x in v[:3])
        return tuple(v)

    out = pymupdf.open()
    job = Path(target).parent
    for page_ir in plan['pages']:
        ox = page_ir['artboard'][0]
        page = out.new_page(width=page_ir['width'], height=page_ir['height'])

        def pt(x, y):
            return pymupdf.Point(x - ox, -y)

        def draw(subpaths, fill, stroke, width, even_odd, opacity):
            shape = page.new_shape()
            for sp in subpaths:
                pts = sp['points']
                count = len(pts) if sp['closed'] else len(pts) - 1
                for i in range(count):
                    a, b = pts[i], pts[(i + 1) % len(pts)]
                    shape.draw_bezier(pt(a[0], a[1]), pt(a[4], a[5]), pt(b[2], b[3]), pt(b[0], b[1]))
            shape.finish(fill=colour(fill), color=colour(stroke), width=width or 0, even_odd=even_odd,
                         closePath=False, fill_opacity=opacity, stroke_opacity=opacity)
            shape.commit()

        for item in page_ir['items']:
            if item['kind'] == 'path':
                draw(item['subpaths'], item['fill'], item['stroke'], item['stroke_width'], item['even_odd'], item['opacity'])
            elif item['kind'] == 'image':
                cx, cy = item['center']
                rect = pymupdf.Rect(cx - ox - item['width'] / 2, -cy - item['height'] / 2,
                                    cx - ox + item['width'] / 2, -cy + item['height'] / 2)
                page.insert_image(rect, filename=str(job / item['file']), rotate=0, keep_proportion=False)
            elif item.get('outline'):
                draw(item['outline'], item['fill'], item['stroke'], item['stroke_width'], False, item['opacity'])
            elif live_text_fallback and item['fill']:
                page.insert_text(pt(*item['origin']), item['text'], fontsize=item['size'], color=colour(item['fill']))
    out.save(target)
    out.close()
    return str(target)


def compare_pdfs(reference, candidate, scale=1.0):
    """Fraction of pixels whose grey difference exceeds 32, per page."""
    import pymupdf
    from PIL import Image, ImageChops
    rows = []
    with pymupdf.open(reference) as a, pymupdf.open(candidate) as b:
        for pa, pb in zip(a, b):
            images = []
            for page in (pa, pb):
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                images.append(Image.frombytes('RGB', (pix.width, pix.height), pix.samples))
            diff = ImageChops.difference(*images).convert('L')
            rows.append(round(sum(diff.histogram()[33:]) / (diff.width * diff.height), 5))
    return rows
