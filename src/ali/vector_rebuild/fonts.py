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


FREE_LICENCE = ('SIL Open Font License', 'Apache License', 'OFL')


def licence_file(font):
    """The licence text that came with a pool face (OFL.txt, LICENSE*, <Family>.LICENSE.txt), if any."""
    p = Path(font.path)
    stem = p.stem.split('_')[0].split('-')[0].lower()
    for c in sorted(p.parent.iterdir()):
        n = c.name.lower()
        if c.suffix.lower() in ('.txt', '') and ('licen' in n or n.startswith('ofl')) and (n.startswith(stem) or n in ('ofl.txt', 'license.txt')):
            return c
    return None


def ship_fonts(postscripts, fonts, out_dir):
    """Copy the faces a build uses, with their licence, into out_dir/fonts when the licence allows it (OFL, Apache),
    so whoever opens the .ai can install them. Faces without such a licence are listed, not copied."""
    import json
    import shutil
    by_ps = {f.postscript: f for f in fonts}
    dest = Path(out_dir) / 'fonts'
    report = {'shipped': [], 'not_shipped': []}
    for ps in sorted(set(postscripts)):
        f = by_ps.get(ps)
        lic = licence_file(f) if f else None
        text = lic.read_text(errors='ignore') if lic else ''
        if f and any(k in text for k in FREE_LICENCE):
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f.path, dest / Path(f.path).name)
            shutil.copy2(lic, dest / lic.name)
            report['shipped'].append({'postscript': ps, 'file': Path(f.path).name, 'licence': lic.name})
        else:
            report['not_shipped'].append({'postscript': ps, 'reason': 'no free licence found with the font file'})
    if report['shipped'] or report['not_shipped']:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / 'fonts.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        (dest / 'README-zh-TW.txt').write_text(
            '開啟 scene.ai 前，請先安裝本資料夾內的字型（雙擊字型檔 > 安裝）。\n'
            '這些字型採 SIL Open Font License 或 Apache License，授權條款與字型放在一起。\n'
            '未附上的字型列在 fonts.json 的 not_shipped，請改用 Adobe Fonts 或自行取得授權。\n', encoding='utf-8')
    return report
