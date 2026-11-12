"""Font candidates for vector rebuild: outline drawing, ink measurement and rasterisation (no Illustrator)."""
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

# Weight instances made from a variable font, when the static file is missing.
VARIABLE_WEIGHTS = {'Medium': 500, 'Bold': 700, 'Black': 900}


class Font:
    def __init__(self, path, postscript=None):
        self.path = str(path)
        self.tt = TTFont(self.path)
        self.postscript = postscript or self.tt['name'].getDebugName(6)
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
            self.glyphset[name].draw(TransformPen(pen, (a * k, b * k, -c * k, -d * k, a * x + e, b * x + f)))
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
            fonts.append(Font(item['file'], item.get('postscript')))
    return fonts
