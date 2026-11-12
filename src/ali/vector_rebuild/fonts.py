"""Font candidates for vector rebuild: outline drawing, ink measurement and rasterisation (no Illustrator)."""
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

# Weight instances made from a variable font, when the static file is missing.
VARIABLE_WEIGHTS = {'Medium': 500, 'Bold': 700, 'Black': 900}


class Font:
    def __init__(self, path, postscript=None, number=0):
        self.path = str(path)
        self.number = number
        self.tt = TTFont(self.path, fontNumber=number) if self.path.lower().endswith(('.ttc', '.otc')) else TTFont(self.path)
        self.postscript = postscript or self.tt['name'].getDebugName(6)
        self.family = self.tt['name'].getDebugName(16) or self.tt['name'].getDebugName(1)
        self.style = self.tt['name'].getDebugName(17) or self.tt['name'].getDebugName(2)
        self.upm = self.tt['head'].unitsPerEm
        self.cmap = self.tt.getBestCmap()
        self.glyphset = self.tt.getGlyphSet()
        self.is_cjk = ord('國') in self.cmap

    def covers(self, text):
        return all(ord(ch) in self.cmap for ch in text)

    def advance(self, ch, size):
        return self.tt['hmtx'][self.cmap[ord(ch)]][0] * size / self.upm

    def draw(self, text, size, matrix, pen, tracking=0.0):
        """Draw text into pen. matrix (a,b,c,d,e,f) maps y-down text space (baseline at y=0) to the page.
        tracking is extra advance per character in em."""
        a, b, c, d, e, f = matrix
        k = size / self.upm
        x = 0.0
        for ch in text:
            name = self.cmap.get(ord(ch))
            if name is None:
                raise ValueError('%s has no glyph for %r' % (self.postscript, ch))
            # composite glyphs (accents, some dashes) are flattened here: drawing pens need not resolve components
            rec = DecomposingRecordingPen(self.glyphset)
            self.glyphset[name].draw(rec)
            rec.replay(TransformPen(pen, (a * k, b * k, -c * k, -d * k, a * x + e, b * x + f)))
            x += self.tt['hmtx'][name][0] * k + tracking * size
        return x

    def ink(self, text, size, matrix, tracking=0.0):
        pen = BoundsPen(None)
        self.draw(text, size, matrix, pen, tracking)
        return pen.bounds


def instance_variable(variable_path, weight_name, out_dir):
    """Make NotoSansTC-<Weight>.ttf style static instances from a variable font (cached)."""
    from fontTools.varLib import instancer
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = TTFont(variable_path)
    family_name = base['name'].getDebugName(16) or base['name'].getDebugName(1)
    for w in ('Thin', 'Light', 'Regular'):
        family_name = family_name.replace(' ' + w, '')
    family = family_name.replace(' ', '')
    target = out_dir / ('%s-%s.ttf' % (family, weight_name))
    if not target.exists():
        inst = instancer.instantiateVariableFont(base, {'wght': VARIABLE_WEIGHTS[weight_name]}, updateFontNames=False)
        name = inst['name']
        full = '%s %s' % (family_name, weight_name)
        for rec in list(name.names):
            if rec.nameID in (1, 4):
                rec.string = full
            elif rec.nameID == 2:
                rec.string = 'Regular'
            elif rec.nameID == 6:
                rec.string = '%s-%s' % (family, weight_name)
            elif rec.nameID in (16, 17, 25):
                rec.string = family_name if rec.nameID == 16 else weight_name
        inst['OS/2'].usWeightClass = VARIABLE_WEIGHTS[weight_name]
        inst.save(str(target))
    return target


def load_candidates(config, cache_dir):
    """config: list of {"file": path, "postscript": name} or {"variable": path, "weights": [...]}"""
    fonts = []
    for item in config:
        if 'variable' in item:
            for weight in item.get('weights', list(VARIABLE_WEIGHTS)):
                path = instance_variable(item['variable'], weight, cache_dir)
                fonts.append(Font(path))
        else:
            fonts.extend(_file_fonts(item['file'], item.get('postscript'), item.get('number')))
    return fonts


def _file_fonts(path, postscript=None, number=None):
    """Every face of a font file (.ttc/.otc collections hold several), or the one numbered."""
    if str(path).lower().endswith(('.ttc', '.otc')):
        from fontTools.ttLib import TTCollection
        count = len(TTCollection(str(path)).fonts)
        return [Font(path, postscript if number is not None else None, k) for k in ([number] if number is not None else range(count))]
    return [Font(path, postscript)]


def font_pool(folder):
    """Candidate faces from every .ttf/.otf/.ttc in folder (the machine's installed fonts, copied), heavy sans first is up to the caller."""
    faces, seen = [], set()
    for f in sorted(Path(folder).iterdir()):
        if f.suffix.lower() not in ('.ttf', '.otf', '.ttc', '.otc'):
            continue
        try:
            for face in _file_fonts(f):
                if face.postscript in seen or 'glyf' not in face.tt and 'CFF ' not in face.tt and 'CFF2' not in face.tt:
                    continue
                seen.add(face.postscript)
                faces.append(face)
        except Exception:
            continue
    return faces


def installed_names(path):
    """PostScript names the target app can set: a JSON list of names or of {"name": ...} records (Illustrator's
    app.textFonts), or one name per line."""
    import json
    text = Path(path).read_text(encoding='utf-8-sig')
    try:
        data = json.loads(text)
    except ValueError:
        return {l.strip() for l in text.splitlines() if l.strip()}
    return {d['name'] if isinstance(d, dict) else str(d) for d in data}


def pool_candidates(folders, names_file=None, exclude=()):
    """Upright Latin faces from the folders, limited to the faces the target app has (a face it cannot set would be
    substituted on open). Every face is a candidate for the closest match; none is assumed to be the source font."""
    allowed = installed_names(names_file) if names_file else None
    out, seen = [], set(exclude)
    for folder in folders:
        for f in font_pool(folder):
            if f.postscript in seen or (allowed is not None and f.postscript not in allowed):
                continue
            if not f.covers('ABCDEFGHIJKLMNOPQRSTUVWXYZ') or f.is_cjk or _sloped(f):
                continue
            seen.add(f.postscript)
            out.append(f)
    return out


def _sloped(f):
    import re
    name = ('%s %s' % (f.postscript, f.style or '')).lower()
    return bool(f.tt['OS/2'].fsSelection & 1) or bool(re.search(r'ital|oblique|slant|kursiv', name))
