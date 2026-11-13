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
        self.adobe = None  # set by adobe_substitutes / adobe_candidates: drawn with this file, named and left unembedded as an Adobe Fonts face
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
        if f is not None and f.adobe:
            report.setdefault('adobe_fonts', []).append({k: f.adobe.get(k) for k in ('postscript', 'family', 'style', 'url')})
            continue
        lic = licence_file(f) if f else None
        text = lic.read_text(errors='ignore') if lic else ''
        if f and any(k in text for k in FREE_LICENCE):
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f.path, dest / Path(f.path).name)
            shutil.copy2(lic, dest / lic.name)
            report['shipped'].append({'postscript': ps, 'file': Path(f.path).name, 'licence': lic.name})
        else:
            report['not_shipped'].append({'postscript': ps, 'reason': 'no free licence found with the font file'})
    if report['shipped'] or report['not_shipped'] or report.get('adobe_fonts'):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / 'fonts.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        (dest / 'README-zh-TW.txt').write_text(
            '開啟 scene.ai 前，請先安裝本資料夾內的字型（雙擊字型檔 > 安裝）。\n'
            '這些字型採 SIL Open Font License 或 Apache License，授權條款與字型放在一起。\n'
            '未附上的字型列在 fonts.json 的 not_shipped，請改用 Adobe Fonts 或自行取得授權。\n'
            + ''.join('%s %s 來自 Adobe Fonts（%s）：有 Creative Cloud 的 Illustrator 開檔時會自動啟用，或先到該網址啟用。\n'
                      % (a.get('family') or a['postscript'], a.get('style') or '', a.get('url') or 'fonts.adobe.com')
                      for a in report.get('adobe_fonts', [])), encoding='utf-8')
    return report


def load_adobe_fonts(path):
    """Adobe Fonts faces to use instead of a free face family: JSON list of
    {"postscript", "family", "style", "url", "replaces_family", "proxy"?, "metrics"?}. The cloud has no Adobe outlines.
    With "metrics" (a JSON file measured in Illustrator, see adobe_candidates) the face competes in the closest-face
    match and is laid out with its own advances and ink boxes; without, the replaced free face is the layout proxy.
    Illustrator activates the Adobe face when the client opens the file."""
    base = Path(path).parent
    import json
    rows = json.loads(Path(path).read_text())
    for r in rows:
        missing = {'postscript', 'replaces_family'} - set(r)
        if missing:
            raise ValueError('adobe font entry %r lacks %s' % (r, ', '.join(sorted(missing))))
        if isinstance(r.get('metrics'), str):
            r['metrics'] = str((base / r['metrics']).resolve())
    return rows


def adobe_substitutes(spec, fonts, adobe_rows):
    """Rename the faces of replaced families to their Adobe Fonts face in spec (in place). Returns the proxy Font
    objects added to fonts, one per Adobe face used."""
    by_ps = {f.postscript: f for f in fonts}
    by_family = {r['replaces_family'].lower(): r for r in adobe_rows}
    added = {}

    def swap(ps):
        f = by_ps.get(ps)
        row = by_family.get((f.family or '').lower()) if f is not None and not f.adobe else None
        if not row:
            return ps
        if row['postscript'] not in added:
            proxy = Font(f.path, number=f.number)
            proxy.postscript = row['postscript']
            proxy.adobe = dict(row, proxy=f.postscript)
            added[row['postscript']] = proxy
        return row['postscript']

    def walk(node):
        if isinstance(node, dict):
            if 'font_match' in node and isinstance(node['font_match'], dict):
                m = node['font_match']
                new = swap(m.get('postscript'))
                if new != m.get('postscript'):
                    row = added[new].adobe
                    node['font_match'] = dict(m, postscript=new, family=row.get('family'), style=row.get('style'),
                                              source='adobe_fonts', url=row.get('url'), layout_proxy=m.get('postscript'))
            for k, v in node.items():
                if k == 'font' and isinstance(v, str):
                    node[k] = swap(v)
                elif k != 'font_match':
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(spec)
    fonts.extend(added.values())
    return list(added.values())


def metric_proxy(proxy_path, metrics, out_path):
    """A stand-in face for an Adobe Fonts face the cloud cannot download: the free proxy's glyphs, each moved and scaled
    into the Adobe face's measured ink box and given its measured advance (metrics: {"units_per_em", "glyphs":
    {char: [advance, xMin, yMin, xMax, yMax]}}, measured in Illustrator). Layout, fitting and preview then follow the
    Adobe face's metrics. Never shipped: the build names the Adobe face without embedding this file."""
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    tt = TTFont(str(proxy_path))
    glyf, hmtx = tt['glyf'], tt['hmtx']
    cmap = tt.getBestCmap()
    k = tt['head'].unitsPerEm / float(metrics.get('units_per_em', 1000))
    gs = tt.getGlyphSet()
    for ch, row in metrics['glyphs'].items():
        name = cmap.get(ord(ch))
        if name is None:
            continue
        adv = row[0] * k
        g = glyf[name]
        if g.isComposite():
            pen = TTGlyphPen(gs)
            rec = DecomposingRecordingPen(gs)
            gs[name].draw(rec)
            rec.replay(pen)
            g = glyf[name] = pen.glyph()
        if len(row) == 5 and g.numberOfContours:
            g.recalcBounds(glyf)
            x0, y0, x1, y1 = g.xMin, g.yMin, g.xMax, g.yMax
            X0, Y0, X1, Y1 = (v * k for v in row[1:])
            sx = (X1 - X0) / max(x1 - x0, 1)
            sy = (Y1 - Y0) / max(y1 - y0, 1)
            coords = g.coordinates
            for i in range(len(coords)):
                x, y = coords[i]
                coords[i] = (round(X0 + (x - x0) * sx), round(Y0 + (y - y0) * sy))
            if hasattr(g, 'program'):
                from fontTools.ttLib.tables import ttProgram
                g.program = ttProgram.Program()
                g.program.fromBytecode(b'')
            g.recalcBounds(glyf)
            hmtx[name] = (round(adv), g.xMin)
        else:
            hmtx[name] = (round(adv), hmtx[name][1])
    # a modified font must not carry the original's (reserved) names
    name = tt['name']
    for rec in list(name.names):
        if rec.nameID in (1, 3, 4, 6, 16, 17, 21, 22):
            name.removeNames(nameID=rec.nameID)
    for nid, val in ((1, 'ALI Metric Stand-in'), (2, 'Regular'), (3, 'ALI Metric Stand-in %s' % metrics.get('postscript', '')),
                     (4, 'ALI Metric Stand-in'), (6, 'ALIMetricStandIn-%s' % metrics.get('postscript', 'Face'))):
        name.setName(val, nid, 3, 1, 0x409)
        name.setName(val, nid, 1, 0, 0)
    for tag in ('GPOS', 'kern', 'fpgm', 'prep', 'cvt ', 'hdmx', 'LTSH', 'VDMX'):
        if tag in tt:
            del tt[tag]
    tt.save(str(out_path))
    return out_path


def adobe_candidates(adobe_rows, fonts, work_dir):
    """Adobe Fonts faces with measured metrics become candidates in place of the free family they replace (laid out by
    metric_proxy). Returns (candidates, rows without metrics)."""
    import json
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    out, rest, replaced = [f for f in fonts], [], set()
    for row in adobe_rows:
        if not row.get('metrics'):
            rest.append(row)
            continue
        metrics = row['metrics'] if isinstance(row['metrics'], dict) else json.loads(Path(row['metrics']).read_text())
        fam = row['replaces_family'].lower()
        family = [f for f in fonts if (f.family or '').lower() == fam]
        proxy = next((f for f in family if f.postscript == row.get('proxy')), family[0] if family else None)
        if proxy is None:
            raise ValueError('no %s face in the font pool to lay out %s' % (row['replaces_family'], row['postscript']))
        path = metric_proxy(proxy.path, metrics, work / ('%s.metric-proxy.ttf' % row['postscript']))
        f = Font(path)
        f.postscript = row['postscript']
        f.family, f.style = row.get('family') or f.family, row.get('style') or f.style
        f.adobe = dict({k: v for k, v in row.items() if k != 'metrics'}, proxy=proxy.postscript)
        chars = set(metrics['glyphs'])
        f.covers = lambda text, chars=chars: all(ch in chars for ch in text)
        replaced.add(fam)
        out.append(f)
    out = [f for f in out if f.adobe or (f.family or '').lower() not in replaced]
    return out, rest
