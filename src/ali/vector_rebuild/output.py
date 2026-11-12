"""Write the rebuilt artwork: SVG with named groups and live text, and a vector PDF (PDF-compatible .ai)."""
import re

from fontTools.pens.svgPathPen import SVGPathPen

from .text import NOMINAL


def _mat(m):
    return 'matrix(%s)' % ' '.join('%.5f' % v for v in m)


def _esc(t):
    return t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def text_layer(frames, fonts):
    """frames: dicts with name, text, font (postscript), m, tracking, fill, runs, decorations."""
    by_ps = {f.postscript: f for f in fonts}
    decos, texts = [], []
    for fr in frames:
        font = by_ps[fr['font']]
        for dec in fr.get('decorations', []):
            pen = SVGPathPen(None)
            m = fr['m'][:5] + (fr['m'][5] + dec['dy'],)
            font.draw(fr['text'], NOMINAL, m, pen, fr.get('tracking', 0))
            el = '<path id="%s_%s" d="%s" fill="%s" stroke="%s" stroke-width="%.2f" stroke-linejoin="round"/>' % (
                fr['name'], dec['name'], pen.getCommands(), dec['color'], dec['color'], dec['stroke'])
            decos.insert(0, el) if dec['name'] == 'SHADOW' else decos.append(el)
        body, last = [], 0
        for r in fr.get('runs', []):
            body.append(_esc(fr['text'][last:r['start']]))
            st = ' stroke="%s" stroke-width="%s"' % (r['stroke'], r['stroke_width']) if r.get('stroke') else ''
            body.append('<tspan fill="%s"%s>%s</tspan>' % (r['fill'], st, _esc(fr['text'][r['start']:r['end']])))
            last = r['end']
        body.append(_esc(fr['text'][last:]))
        ls = ' letter-spacing="%.3f"' % (fr['tracking'] * NOMINAL) if fr.get('tracking') else ''
        texts.append('<text id="%s" transform="%s" font-family="%s" font-size="%s" fill="%s"%s xml:space="preserve">%s</text>' % (
            fr['name'], _mat(fr['m']), fr['font'], NOMINAL, fr['fill'], ls, ''.join(body)))
    return decos, texts


def write_pdf(stages, fonts, page_size, out_pdf, work_dir):
    """stages: [(svg, frames), ...] painted in order - each stage's artwork, then its live text on top.
    Artwork goes through cairo (keeps gradients as shadings, no per-object clip groups), live text through PyMuPDF."""
    import cairosvg
    import pymupdf
    W, H = page_size
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    by_ps = {f.postscript: f for f in fonts}
    used = {}

    def rgb(h):
        return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))

    run_streams = []
    fi = -1
    for si, (svg, frames) in enumerate(stages):
        art = re.sub(r'<text .*?</text>', '', svg, flags=re.S)
        if re.search(r'<(path|rect|circle|ellipse|polygon|line|g id="[^"]+">\s*<)', art):
            art_pdf = work_dir / ('artwork-%d.pdf' % si)
            cairosvg.svg2pdf(bytestring=art.encode(), write_to=str(art_pdf))
            with pymupdf.open(art_pdf) as spdf:
                page.show_pdf_page(page.rect, spdf, 0, keep_proportion=False)
            art_pdf.unlink()
        for fr in frames:
            if fr['font'] not in used:
                used[fr['font']] = 'F%d' % len(used)
                page.insert_font(fontname=used[fr['font']], fontfile=by_ps[fr['font']].path)
        for fr in frames:
            fi += 1
            a, b, c, d, e, f_ = fr['m']
            font = by_ps[fr['font']]
            n = len(fr['text'])
            colours, strokes = [fr['fill']] * n, [None] * n
            for r in fr.get('runs', []):
                for i in range(r['start'], r['end']):
                    colours[i], strokes[i] = r['fill'], r.get('stroke')
            x, i = 0.0, 0
            track = fr.get('tracking', 0) * NOMINAL
            while i < n:
                j = i
                while j < n and colours[j] == colours[i] and strokes[j] == strokes[i]:
                    j += 1
                origin = pymupdf.Point(a * x + e, b * x + f_)
                page.insert_text(origin, fr['text'][i:j], fontname=used[fr['font']], fontsize=NOMINAL, color=rgb(colours[i]),
                                 morph=(origin, pymupdf.Matrix(a, -b, -c, d, 0, 0)),
                                 render_mode=2 if strokes[i] else 0, border_width=(.65 / NOMINAL) if strokes[i] else 0.05,
                                 fill=rgb(colours[i]) if strokes[i] else None, fill_opacity=fr.get('opacity', 1),
                                 stroke_opacity=fr.get('opacity', 1))
                xref = page.get_contents()[-1]
                if track:
                    st = doc.xref_stream(xref).decode('latin-1')
                    doc.update_stream(xref, st.replace(' Tf ', ' Tf %.4f Tc ' % track, 1).encode('latin-1'))
                run_streams.append((fi, xref))
                for ch in fr['text'][i:j]:
                    x += font.advance(ch, NOMINAL) + track
                i = j
    # One BT…ET per frame so Illustrator keeps a frame together.
    first = {}
    for fi, xref in run_streams:
        st = doc.xref_stream(xref).decode('latin-1')
        if fi not in first:
            first[fi] = xref
            continue
        body = re.search(r'Tf (.*?TJ)', st, re.S).group(1)
        if ' Tr ' not in body:
            body = '0 Tr ' + body
        head = doc.xref_stream(first[fi]).decode('latin-1')
        k = head.rindex('ET')
        doc.update_stream(first[fi], (head[:k] + body + '\n' + head[k:]).encode('latin-1'))
        doc.update_stream(xref, b' ')
    doc.subset_fonts()
    # Illustrator matches fonts by PostScript name.
    names = {f.tt['name'].getDebugName(4): f.postscript for f in fonts}
    names.update({f.tt['name'].getDebugName(6): f.postscript for f in fonts})
    # Some fonts (e.g. Impact) are embedded as "<family> <subfamily>", which Illustrator cannot match.
    names.update({'%s %s' % (f.tt['name'].getDebugName(1), f.tt['name'].getDebugName(2)): f.postscript for f in fonts})
    for x in range(1, doc.xref_length()):
        for key in ('BaseFont', 'FontName'):
            t, v = doc.xref_get_key(x, key)
            if t != 'name':
                continue
            raw = v.lstrip('/').replace('#20', ' ')
            tag, _, base = raw.rpartition('+')
            for full, ps in names.items():
                if full and base.replace(' ', '') == full.replace(' ', ''):
                    doc.xref_set_key(x, key, '/' + (tag + '+' if tag else '') + ps)
    # Adobe Fonts faces: the cloud only has a layout proxy, which must not be shipped under the Adobe name.
    # Leave them unembedded so Illustrator activates (or substitutes) the real face by name.
    adobe = {f.postscript for f in fonts if getattr(f, 'adobe', None)}
    if adobe:
        # what the cloud can render: proxy outlines in place of the Adobe faces (cloud preview and scoring only)
        doc.save(work_dir / 'layout-proxy.pdf', garbage=4, deflate=True)
        for x in range(1, doc.xref_length()):
            for key in ('BaseFont', 'FontName'):
                t, v = doc.xref_get_key(x, key)
                if t == 'name' and v.lstrip('/').rpartition('+')[2] in adobe:
                    doc.xref_set_key(x, key, '/' + v.lstrip('/').rpartition('+')[2])
            for key in ('FontFile', 'FontFile2', 'FontFile3', 'CIDSet'):
                if doc.xref_get_key(x, 'FontName')[1].lstrip('/') in adobe and doc.xref_get_key(x, key)[0] != 'null':
                    doc.xref_set_key(x, key, 'null')
    doc.save(out_pdf, garbage=4, deflate=True)
    doc.close()
