"""Build a scene spec (see scene.py) the way the accepted 2026-11-08 jobs were built.

Cloud route: SVG (named groups, native gradients, live text) and a vector PDF / PDF-compatible .ai, painted
element by element in stacking order: shadow image, plate (gradient rectangle, end fade, material image),
then the element's live text.
Native route: build-illustrator.jsx, a port of the accepted Illustrator scripts (ink fitting by createOutline,
tapes rotated as groups, ribbons with gradient + fade + repeated live words, embedded shadow/material images),
so the same spec can be opened as a layered native document on a machine with Illustrator.
"""
import base64
import io
import json
import math
import shutil
from pathlib import Path

import cv2
import numpy as np

from .scene import to_local, to_page
from .text import NOMINAL, CairoPen, fit_matrix

SHADOW_PAD = 3.0  # blur radii kept around the silhouette


def _hex(c):
    return '#%02x%02x%02x' % tuple(int(np.clip(round(v), 0, 255)) for v in c)


def _png(rgba):
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgba).save(buf, 'PNG', optimize=True)
    return buf.getvalue()


def _alpha_text(font, text, m, tracking, shape):
    import cairocffi as cairo
    h, w = shape
    surface = cairo.ImageSurface(cairo.FORMAT_A8, w, h)
    ctx = cairo.Context(surface)
    font.draw(text, NOMINAL, m, CairoPen(ctx), tracking)
    ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
    ctx.fill()
    surface.flush()
    data = np.frombuffer(surface.get_data(), np.uint8).reshape(h, surface.get_stride())[:, :w]
    return data.astype(np.float32) / 255


def _alpha_rect(cx, cy, theta, w, h, shape):
    pts = [to_page(cx, cy, theta, u, v) for u, v in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]
    m = np.zeros(shape, np.uint8)
    cv2.fillPoly(m, [np.round(np.array(pts) * 16).astype(np.int32)], 255, cv2.LINE_AA, shift=4)
    return m.astype(np.float32) / 255


def shadow_image(alpha, components):
    """Gaussian convolution of the element's own alpha, components alpha-unioned (accepted method)."""
    acc = np.zeros_like(alpha)
    H, W = alpha.shape
    for dx, dy, blur, op in components:
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        a = cv2.warpAffine(alpha, M, (W, H))
        if blur > 0:
            a = cv2.GaussianBlur(a, (0, 0), blur)
        acc = 1 - (1 - acc) * (1 - a * op)
    return acc


def crop_rgba(rgb, alpha, min_alpha=1 / 255):
    ys, xs = np.nonzero(alpha >= min_alpha)
    if len(xs) == 0:
        return None
    x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    out = np.dstack([np.broadcast_to(rgb, alpha.shape + (3,))[y0:y1, x0:x1] if np.ndim(rgb) == 1 else rgb[y0:y1, x0:x1],
                     np.round(alpha[y0:y1, x0:x1] * 255)]).astype(np.uint8)
    return int(x0), int(y0), out


def material_image(el, alpha):
    """Accepted blue-ribbon material: fitted colour field + seeded fine noise, fibres and weave."""
    from PIL import Image
    mat = el['material']
    H, W = alpha.shape
    y, x = np.mgrid[0:H, 0:W].astype(np.float64)
    theta = -math.radians(el['rotation_deg'])
    u, v = to_local(el['center'][0], el['center'][1], theta, x, y)
    ur, vr = u / mat['u_scale'], v / (el['size'][1] / 2)
    X = np.stack([np.ones_like(ur), ur, ur * ur, vr, vr * vr, vr ** 3, ur * vr, np.exp(-(vr / .42) ** 2)], 2)
    color = X @ np.array(mat['rgb_coefficients'])
    rng = np.random.default_rng(mat['seed'])
    fine = rng.normal(0, mat['fine_noise_std'], (H, W))
    small = rng.normal(128, 28, (max(2, H // 8), max(2, W // 2))).clip(0, 255).astype('uint8')
    fib = np.array(Image.fromarray(small).resize((W, H), Image.Resampling.BILINEAR), float) - 128
    weave = np.sin(u * 2.6 + v * .8) * .65 + np.sin(v * 4.5) * .7
    color += (fine + fib * mat['fibre_gain'] + weave)[:, :, None] * np.array([.65, .85, 1.0])
    return color.clip(0, 255).astype(np.uint8)


def text_frames(el, fonts):
    """Live text of an element as page matrices (same rule as the accepted builds: ink box -> non-uniform scale)."""
    by_ps = {f.postscript: f for f in fonts}
    out = []
    if el['kind'] == 'title':
        out.append({'name': el['id'], 'text': el['text'], 'font': el['font'], 'm': tuple(el['m']), 'tracking': el.get('tracking', 0),
                    'fill': _hex(el['color'])})
        return out
    cx, cy = el['center']
    theta = -math.radians(el['rotation_deg'])
    co, si = math.cos(theta), math.sin(theta)

    def place(name, text, fontname, box, tracking, color, opacity=1.0):
        f = by_ps[fontname]
        x0, y0, x1, y1 = box
        a, b, c, d, e, g = fit_matrix(f, text, (x0, y0, x1 - x0, y1 - y0), 0, tracking)
        ex, ey = to_page(cx, cy, theta, e, g)
        m = (co * a - si * b, si * a + co * b, co * c - si * d, si * c + co * d, ex, ey)
        out.append({'name': name, 'text': text, 'font': fontname, 'm': m, 'tracking': tracking, 'fill': _hex(color), 'opacity': opacity,
                    'box': box})

    for k, t in enumerate(el.get('texts', [])):
        place('%s_TEXT_%d' % (el['id'], k), t['text'], t['font'], t['box'], t.get('tracking', 0), t['color'])
    rep = el.get('repeat')
    if rep and rep.get('font'):
        iw, ih = rep['ink_size']
        for n in range(rep['n'][0], rep['n'][1] + 1):
            uc = rep['origin_u'] + n * rep['pitch'] + rep['copy_offset']
            if abs(uc) - iw / 2 > el['size'][0] / 2:
                continue
            op = rep.get('opacity', {}).get(str(n), 100) / 100
            place('%s_REPEAT_%d' % (el['id'], n), rep['word'], rep['font'], (uc - iw / 2, rep['ink_v'], uc + iw / 2, rep['ink_v'] + ih),
                  rep.get('tracking', 0), rep['color'], op)
    return out


def _gradient_bg(bg, W, H):
    stops = ''.join('<stop offset="%.3f" stop-color="%s"/>' % (o / 100, _hex(c)) for o, c in bg['stops'])
    if bg['type'] == 'radial':
        return ('<radialGradient id="bg" gradientUnits="userSpaceOnUse" cx="%.2f" cy="%.2f" r="%.2f">%s</radialGradient>'
                % (bg['origin'][0], bg['origin'][1], bg['length'], stops))
    a = math.radians(bg['angle_deg'])
    x1, y1 = bg['start'] * math.cos(a), bg['start'] * math.sin(a)
    x2, y2 = bg['end'] * math.cos(a), bg['end'] * math.sin(a)
    return ('<linearGradient id="bg" gradientUnits="userSpaceOnUse" x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f">%s</linearGradient>'
            % (x1, y1, x2, y2, stops))


def _image(x, y, rgba, name):
    return '<image id="%s" x="%d" y="%d" width="%d" height="%d" href="data:image/png;base64,%s"/>' % (
        name, x, y, rgba.shape[1], rgba.shape[0], base64.b64encode(_png(rgba)).decode())


def element_art(el, k, shape, fonts, assets):
    """SVG for one element's artwork (no text) and its raster assets (saved for the native route)."""
    H, W = shape
    parts, defs = [], []
    frames = text_frames(el, fonts)
    by_ps = {f.postscript: f for f in fonts}
    if el['kind'] == 'title':
        fr = frames[0]
        alpha = _alpha_text(by_ps[fr['font']], fr['text'], fr['m'], fr['tracking'], shape)
    else:
        theta = -math.radians(el['rotation_deg'])
        alpha = _alpha_rect(el['center'][0], el['center'][1], theta, el['size'][0], el['size'][1], shape)
    if el.get('shadow'):
        sh = crop_rgba(np.zeros(3, np.uint8), shadow_image(alpha, el['shadow']))
        if sh:
            assets.append({'element': k, 'role': 'shadow', 'x': sh[0], 'y': sh[1], 'rgba': sh[2]})
            parts.append(_image(sh[0], sh[1], sh[2], '%s SHADOW' % el['id']))
    if el['kind'] != 'title':
        w, h = el['size']
        f = el['fill']
        tf = 'translate(%.3f %.3f) rotate(%.4f)' % (el['center'][0], el['center'][1], -el['rotation_deg'])
        fade = el.get('fade')
        if f['type'] == 'flat' and fade:
            # Black end fade over a flat plate, baked into one opaque gradient: the same colours, and no
            # transparency for the app that opens the PDF to re-interpret (Illustrator darkened the alpha stops).
            defs.append('<linearGradient id="g%d" gradientUnits="userSpaceOnUse" x1="%.2f" y1="0" x2="%.2f" y2="0">%s</linearGradient>' % (
                k, -w / 2, w / 2, ''.join('<stop offset="%.3f" stop-color="%s"/>' % (o / 100, _hex([round(c * (1 - a / 100)) for c in f['color']]))
                                          for o, a in fade)))
            paint = 'url(#g%d)' % k
            fade = None
        elif f['type'] == 'flat':
            paint = _hex(f['color'])
        else:
            defs.append('<linearGradient id="g%d" gradientUnits="userSpaceOnUse" x1="0" y1="%.2f" x2="0" y2="%.2f">%s</linearGradient>' % (
                k, -h / 2, h / 2, ''.join('<stop offset="%.3f" stop-color="%s"/>' % (o / 100, _hex(c)) for o, c in f['stops'])))
            paint = 'url(#g%d)' % k
        g = ['<rect id="%s PLATE" x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="%s"/>' % (el['id'], -w / 2, -h / 2, w, h, paint)]
        if fade:
            defs.append('<linearGradient id="f%d" gradientUnits="userSpaceOnUse" x1="%.2f" y1="0" x2="%.2f" y2="0">%s</linearGradient>' % (
                k, -w / 2, w / 2, ''.join('<stop offset="%.3f" stop-color="#000000" stop-opacity="%.3f"/>' % (o / 100, a / 100)
                                          for o, a in el['fade'])))
            g.append('<rect id="%s END FADE" x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="url(#f%d)"/>' % (el['id'], -w / 2, -h / 2, w, h, k))
        parts.append('<g transform="%s">%s</g>' % (tf, ''.join(g)))
        if el.get('material'):
            mat = crop_rgba(material_image(el, alpha), alpha)
            if mat:
                assets.append({'element': k, 'role': 'material', 'x': mat[0], 'y': mat[1], 'rgba': mat[2]})
                parts.append(_image(mat[0], mat[1], mat[2], '%s MATERIAL' % el['id']))
    return defs, parts, frames


def _svg_text(fr):
    ls = ' letter-spacing="%.3f"' % (fr['tracking'] * NOMINAL) if fr.get('tracking') else ''
    op = ' opacity="%.3f"' % fr['opacity'] if fr.get('opacity', 1) < 1 else ''
    t = fr['text'].replace('&', '&amp;').replace('<', '&lt;')
    return '<text id="%s" transform="matrix(%s)" font-family="%s" font-size="%s" fill="%s"%s%s xml:space="preserve">%s</text>' % (
        fr['name'], ' '.join('%.5f' % v for v in fr['m']), fr['font'], NOMINAL, fr['fill'], ls, op, t)


def build(spec, fonts, out_dir, log=print):
    from .output import write_pdf
    out = Path(out_dir)
    work = out / 'work'
    work.mkdir(parents=True, exist_ok=True)
    W, H = spec['size']
    head = '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (W, H, W, H)
    bg = '<defs>%s</defs><rect id="01 BACKGROUND" width="%d" height="%d" fill="url(#bg)"/>' % (_gradient_bg(spec['background'], W, H), W, H)
    assets, stages, full = [], [], [bg]
    for k, el in enumerate(spec['elements']):
        defs, parts, frames = element_art(el, k, (H, W), fonts, assets)
        art = ('<defs>%s</defs>' % ''.join(defs) if defs else '') + ''.join(parts)
        stages.append((head + (bg if k == 0 else '') + art + '</svg>', frames))
        full.append('<g id="%s">%s%s</g>' % (el['id'], art, ''.join(_svg_text(fr) for fr in frames)))
    if not stages:
        stages.append((head + bg + '</svg>', []))
    (out / 'scene.svg').write_text(head + ''.join(full) + '</svg>')
    write_pdf(stages, fonts, (W, H), out / 'scene.pdf', work)
    shutil.copyfile(out / 'scene.pdf', out / 'scene.ai')
    native = out / 'native'
    native.mkdir(exist_ok=True)
    for a in assets:
        from PIL import Image
        a['file'] = '%s-%02d.png' % (a['role'], a['element'])
        Image.fromarray(a['rgba']).save(native / a['file'])
    (native / 'build-illustrator.jsx').write_text(illustrator_script(spec, fonts, assets))
    (out / 'scene.json').write_text(json.dumps(spec, ensure_ascii=False, indent=1))
    log('scene build: %d elements, %d raster assets' % (len(spec['elements']), len(assets)))
    return out / 'scene.pdf'


# ---------------------------------------------------------------- native route

JSX_HEAD = r'''#target illustrator
// Generated from scene.json by ali scene-rebuild. Port of the accepted 2026-11-08 Illustrator builds.
var root = File($.fileName).parent.fsName, H = %(H)d, W = %(W)d, doc = null, old = app.userInteractionLevel;
function rgb(c){var x=new RGBColor();x.red=c[0];x.green=c[1];x.blue=c[2];return x;}
function layer(n){var l=doc.layers.add();l.name=n;return l;}
function rect(parent,x,y,w,h,color){var p=parent.pathItems.rectangle(H-y,x,w,h);p.filled=true;p.fillColor=rgb(color);p.stroked=false;return p;}
function ink(t){var a=t.duplicate();var g=a.createOutline();var b=g.geometricBounds;g.remove();return b;}
function fitted(parent,name,content,x,y,w,h,color,font,tracking){var t=parent.textFrames.add();t.name=name;t.contents=content;var ca=t.textRange.characterAttributes;ca.textFont=app.textFonts.getByName(font);ca.size=100;ca.tracking=Math.round(tracking*1000);ca.fillColor=rgb(color);app.redraw();var b=ink(t);t.resize(w/(b[2]-b[0])*100,h/(b[1]-b[3])*100,true,false,false,false,100,Transformation.TOPLEFT);b=ink(t);t.translate(x-b[0],H-y-b[1]);return t;}
function centreTo(item,ref,cx,cy){var b=ref.geometricBounds;item.translate(cx-(b[0]+b[2])/2,(H-cy)-(b[1]+b[3])/2);}
function gradient(name,type,stops){var g=doc.gradients.add();g.name=name;g.type=type;for(var i=0;i<stops.length;i++){var z=i<2?g.gradientStops[i]:g.gradientStops.add();z.rampPoint=stops[i][0];z.color=rgb(stops[i][1]);if(stops[i].length>2)z.opacity=stops[i][2];}return g;}
function place(parent,file,x,y,name){var p=parent.placedItems.add();p.file=File(root+'/'+file);p.position=[x,H-y];p.embed();var e=parent.pageItems[0];e.name=name;return e;}
'''


def illustrator_script(spec, fonts, assets):
    W, H = spec['size']
    lines = [JSX_HEAD % {'H': H, 'W': W}]
    body = ["try{app.userInteractionLevel=UserInteractionLevel.DONTDISPLAYALERTS;if(File(root+'/scene-native.ai').exists)throw Error('Refusing overwrite');",
            "doc=app.documents.add(DocumentColorSpace.RGB,W,H);var defaultLayer=doc.layers[0];"]
    bg = spec['background']
    stops = json.dumps([[s[0], s[1]] for s in bg['stops']])
    if bg['type'] == 'radial':
        body.append("var bgl=layer('00 Background');var bgg=gradient('Background','RADIAL_PLACEHOLDER',%s);var gc=new GradientColor();gc.gradient=bgg;"
                    "gc.origin=[%.2f,H-%.2f];gc.length=%.2f;var bgr=rect(bgl,0,0,W,H,[0,0,0]);bgr.fillColor=gc;" % (
                        stops, bg['origin'][0], bg['origin'][1], bg['length']))
    else:
        a = math.radians(bg['angle_deg'])
        body.append("var bgl=layer('00 Background');var bgg=gradient('Background','LINEAR_PLACEHOLDER',%s);var gc=new GradientColor();gc.gradient=bgg;"
                    "gc.origin=[%.2f,H-%.2f];gc.angle=%.3f;gc.length=%.2f;var bgr=rect(bgl,0,0,W,H,[0,0,0]);bgr.fillColor=gc;" % (
                        stops, bg['start'] * math.cos(a), bg['start'] * math.sin(a), -bg['angle_deg'], bg['end'] - bg['start']))
    by_el = {}
    for a in assets:
        by_el.setdefault(a['element'], []).append(a)
    for k, el in enumerate(spec['elements']):
        nm = json.dumps(el['id'])
        body.append('var L=layer(%s);' % nm)
        if el['kind'] == 'title':
            x0, y0, x1, y1 = el['box']
            fx, fy = el['frame']['cx'], el['frame']['cy']
            body.append("var t=fitted(L,%s,%s,%.2f,%.2f,%.2f,%.2f,%s,%s,%.3f);" % (
                json.dumps(el['id'] + ' TEXT'), json.dumps(el['text']), fx + x0, fy + y0, x1 - x0, y1 - y0, json.dumps(el['color']),
                json.dumps(el['font']), el.get('tracking', 0)))
            if el['frame']['rotation_deg']:
                th = -math.radians(el['frame']['rotation_deg'])
                tcx, tcy = to_page(fx, fy, th, (x0 + x1) / 2, (y0 + y1) / 2)
                body.append("t.rotate(%.3f,true,true,true,true,Transformation.CENTER);var o=t.duplicate().createOutline();centreTo(t,o,%.2f,%.2f);o.remove();" % (
                    el['frame']['rotation_deg'], tcx, tcy))
        else:
            cx, cy = el['center']
            w, h = el['size']
            body.append("var g=L.groupItems.add();g.name=%s;var p=rect(g,%.2f,%.2f,%.2f,%.2f,%s);p.name='PLATE';" % (
                json.dumps(el['id'] + ' PLATE AND TEXT'), cx - w / 2, cy - h / 2, w, h,
                json.dumps(el['fill']['color'] if el['fill']['type'] == 'flat' else el['fill']['stops'][2][1])))
            if el['fill']['type'] != 'flat':
                body.append("var gg=gradient(%s,'LINEAR_PLACEHOLDER',%s);var c=new GradientColor();c.gradient=gg;c.origin=[%.2f,H-%.2f];c.angle=-90;c.length=%.2f;p.fillColor=c;" % (
                    json.dumps(el['id'] + ' across'), json.dumps(el['fill']['stops']), cx, cy - h / 2, h))
            if el.get('fade'):
                body.append("var fr=rect(g,%.2f,%.2f,%.2f,%.2f,[0,0,0]);fr.name='END FADE';var fg=gradient(%s,'LINEAR_PLACEHOLDER',%s);var fc=new GradientColor();fc.gradient=fg;fc.origin=[%.2f,H-%.2f];fc.length=%.2f;fr.fillColor=fc;" % (
                    cx - w / 2, cy - h / 2, w, h, json.dumps(el['id'] + ' fade'), json.dumps([[o, [0, 0, 0], a] for o, a in el['fade']]),
                    cx - w / 2, cy, w))
            frames = text_frames(el, fonts)
            for fr in frames:
                x0, y0, x1, y1 = fr['box']
                body.append("var t=fitted(g,%s,%s,%.2f,%.2f,%.2f,%.2f,%s,%s,%.3f);t.opacity=%.1f;" % (
                    json.dumps(fr['name']), json.dumps(fr['text']), cx + x0, cy + y0, x1 - x0, y1 - y0,
                    json.dumps([int(fr['fill'][i:i + 2], 16) for i in (1, 3, 5)]), json.dumps(fr['font']), fr['tracking'],
                    fr.get('opacity', 1) * 100))
            body.append("g.rotate(%.3f,true,true,true,true,Transformation.CENTER);centreTo(g,p,%.2f,%.2f);" % (el['rotation_deg'], cx, cy))
        for a in by_el.get(k, []):
            if a['role'] == 'material':
                body.append("var mg=L.groupItems.add();mg.name='MATERIAL';place(mg,%s,%d,%d,'Material');mg.move(g,ElementPlacement.PLACEBEFORE);"
                            "while(g.textFrames.length)g.textFrames[g.textFrames.length-1].move(mg,ElementPlacement.PLACEBEFORE);" % (json.dumps(a['file']), a['x'], a['y']))
        for a in by_el.get(k, []):
            if a['role'] == 'shadow':
                body.append("var sg=L.groupItems.add();sg.name='SHADOW';place(sg,%s,%d,%d,'Soft shadow');sg.move(L,ElementPlacement.PLACEATEND);" % (
                    json.dumps(a['file']), a['x'], a['y']))
    body.append("if(defaultLayer.pageItems.length==0&&doc.layers.length>1)defaultLayer.remove();")
    body.append("var o=new IllustratorSaveOptions();o.pdfCompatible=true;doc.saveAs(File(root+'/scene-native.ai'),o);"
                "var ex=new ExportOptionsPNG24();ex.artBoardClipping=true;ex.horizontalScale=100;ex.verticalScale=100;ex.transparency=false;"
                "doc.exportFile(File(root+'/proof-native'),ExportType.PNG24,ex);"
                "var f=File(root+'/build-verification.json');f.open('w');f.write('{\"saved_in_illustrator\":true,\"layers\":'+doc.layers.length+',\"text_frames\":'+doc.textFrames.length+',\"paths\":'+doc.pathItems.length+',\"placed_or_raster\":'+(doc.placedItems.length+doc.rasterItems.length)+'}');f.close();")
    body.append("}catch(e){var f=File(root+'/error.txt');f.open('w');f.write(e.toString()+' line '+e.line);f.close();throw e;}finally{if(doc)doc.close(SaveOptions.DONOTSAVECHANGES);app.userInteractionLevel=old;}")
    s = '\n'.join(lines + body) + '\n'
    return s.replace("'RADIAL_PLACEHOLDER'", 'GradientType.RADIAL').replace("'LINEAR_PLACEHOLDER'", 'GradientType.LINEAR')


def preview_pdf(pdf):
    """The PDF the cloud can render: scene.pdf, or its layout-proxy copy when Adobe Fonts faces are left unembedded."""
    proxy = Path(pdf).parent / 'work' / 'layout-proxy.pdf'
    return proxy if proxy.exists() else Path(pdf)


def score(pdf, src):
    import pymupdf
    pdf = preview_pdf(pdf)
    H, W = src.shape[:2]
    with pymupdf.open(pdf) as d:
        pix = d[0].get_pixmap(matrix=pymupdf.Matrix(W / d[0].rect.width, H / d[0].rect.height), alpha=False)
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    img = cv2.resize(img, (W, H)) if img.shape[:2] != (H, W) else img
    return float(np.abs(img.astype(np.float32) - src.astype(np.float32)).mean()), img
