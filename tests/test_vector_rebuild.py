import json
import math
from pathlib import Path

import pytest

from ali.vector_rebuild.job import guess_locale, parse_font_args, run_job


def test_font_args_parse_files_postscript_and_variable(tmp_path):
    a = tmp_path / 'A.ttf'
    v = tmp_path / 'V.ttf'
    a.write_bytes(b'x')
    v.write_bytes(b'x')
    cfg = parse_font_args(['%s=Arial-BoldMT' % a, 'variable:%s' % v, str(tmp_path / 'missing.ttf')], tmp_path)
    assert cfg == [{'file': str(a), 'postscript': 'Arial-BoldMT'}, {'variable': str(v)}]


def test_font_args_without_usable_fonts_fail(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_font_args([str(tmp_path / 'none.ttf')], tmp_path)


def test_locale_guess_from_ocr_text():
    assert guess_locale([{'text': 'EVERY DETAIL'}]) == 'en'
    assert guess_locale([{'text': 'Vertical'}, {'text': '設計展'}]) == 'zh-TW'


def test_run_job_refuses_nonempty_output_and_overlap(tmp_path):
    src = tmp_path / 'in' / 'a.png'
    src.parent.mkdir()
    src.write_bytes(b'png')
    busy = tmp_path / 'busy'
    busy.mkdir()
    (busy / 'old.txt').write_text('keep')
    with pytest.raises(FileExistsError):
        run_job(src, busy)
    with pytest.raises(ValueError):
        run_job(src, src.parent)
    assert (busy / 'old.txt').read_text() == 'keep'


def test_cli_lists_vector_rebuild(capsys):
    from ali.cli import main
    with pytest.raises(SystemExit):
        main(['vector-rebuild', '--help'])
    assert '--font' in capsys.readouterr().out


np = pytest.importorskip('numpy')
cv2 = pytest.importorskip('cv2')


def _disc(H, W, cx, cy, r):
    m = np.zeros((H, W), np.uint8)
    cv2.circle(m, (cx, cy), r, 1, -1)
    return m > 0


def test_shapes_recognise_circle_triangle_and_gradient():
    from ali.vector_rebuild.shapes import fit_geometry, fit_paint
    kind, geo, iou = fit_geometry(_disc(200, 200, 100, 90, 60))
    assert kind == 'ellipse' and abs(geo['rx'] - 60) < 2 and iou > 0.94
    tri = np.zeros((200, 200), np.uint8)
    cv2.fillPoly(tri, [np.array([[20, 180], [180, 180], [100, 20]], np.int32)], 1)
    kind, d, iou = fit_geometry(tri > 0)
    assert kind == 'polygon' and d.count('L') == 2 and iou > 0.93
    img = np.zeros((100, 200, 3), np.uint8)
    img[:, :, 2] = np.linspace(0, 255, 200).astype(np.uint8)[None, :]
    _, fill, err, paint = fit_paint(img, np.ones((100, 200), bool), 'g1')
    assert paint == 'linear' and fill == 'url(#g1)' and err < 3


def test_tiled_masks_join_across_tile_seam_and_partition_is_exclusive():
    from ali.vector_rebuild.segment import merge_masks, partition
    H, W = 120, 200
    whole = np.zeros((H, W), bool)
    whole[30:90, 40:160] = True          # one object cut by the seam at x=100..120
    left = {'x0': 0, 'y0': 0, 'w': 120, 'h': H, 'seg': whole[:, :120], 'score': .9}
    right = {'x0': 80, 'y0': 0, 'w': 120, 'h': H, 'seg': whole[:, 80:], 'score': .8}
    dot = np.zeros((H, 120), bool)
    dot[50:70, 10:30] = True
    small = {'x0': 0, 'y0': 0, 'w': 120, 'h': H, 'seg': dot, 'score': .95}
    kept = merge_masks([left, right, small], H, W)
    assert any((k == whole).all() for k in kept)
    label = partition(kept, H, W)
    assert label.shape == (H, W)
    big = label[60, 100]
    assert big >= 0 and (label[whole] == big).all()
    assert label[60, 20] >= 0 and label[60, 20] != big


LIB = Path('/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf')


def _poster():
    """Dark radial background, a white title and a rotated yellow tape with dark lettering over it."""
    from PIL import Image, ImageDraw, ImageFont
    W, H = 900, 600
    yy, xx = np.mgrid[0:H, 0:W]
    t = np.clip(np.hypot(xx - 450, yy - 300) / 520, 0, 1)[..., None]
    img = Image.fromarray(((1 - t) * 52 + t * 8).repeat(3, 2).astype(np.uint8))
    d = ImageDraw.Draw(img)
    d.text((150, 190), 'HELLO', font=ImageFont.truetype(str(LIB), 190), fill=(240, 240, 238))
    tape = Image.new('RGBA', (340, 64), (250, 218, 18, 255))
    ImageDraw.Draw(tape).text((40, 10), 'TAPE TEXT', font=ImageFont.truetype(str(LIB), 40), fill=(21, 40, 32))
    tape = tape.rotate(8, expand=True, resample=Image.BICUBIC)
    img.paste(tape, (380 - tape.width // 2, 330 - tape.height // 2), tape)
    src = np.array(img)
    c, s = math.cos(math.radians(-8)), math.sin(math.radians(-8))

    def rot(u, v):
        return [380 + u * c - v * s, 330 + u * s + v * c]
    records = [{'text': 'HELLO', 'confidence': .99, 'polygon': [[140, 205], [760, 205], [760, 370], [140, 370]]},
               {'text': 'TAPE TEXT', 'confidence': .99, 'polygon': [rot(-135, -20), rot(135, -20), rot(135, 22), rot(-135, 22)]}]
    return src, records


@pytest.mark.skipif(not LIB.is_file(), reason='Liberation Sans Bold not installed')
def test_scene_finds_tape_title_order_and_builds(tmp_path):
    from ali.vector_rebuild import scene, scene_build
    from ali.vector_rebuild.fonts import Font
    src, records = _poster()
    fonts = [Font(LIB, 'Arial-BoldMT')]
    spec = scene.extract(src, records, fonts, log=lambda s: None)
    kinds = [e['kind'] for e in spec['elements']]
    assert kinds == ['title', 'tape']                       # tape stacked above the title
    tape = spec['elements'][1]
    assert abs(tape['rotation_deg'] - 8) < 1.5
    assert abs(tape['center'][0] - 380) < 8 and abs(tape['center'][1] - 330) < 8
    assert abs(tape['size'][0] - 340) < 14 and abs(tape['size'][1] - 64) < 6
    assert tape['fill']['type'] == 'flat' and np.abs(np.array(tape['fill']['color']) - [250, 218, 18]).max() < 12
    assert tape['texts'][0]['text'] == 'TAPE TEXT'
    title = spec['elements'][0]
    assert title['text'] == 'HELLO' and title['iou'] > .6
    assert spec['background']['type'] == 'radial'
    pdf = scene_build.build(spec, fonts, tmp_path / 'out', log=lambda s: None)
    mae, _ = scene_build.score(pdf, src)
    assert mae < 12
    jsx = (tmp_path / 'out' / 'native' / 'build-illustrator.jsx').read_text()
    assert 'GradientType.RADIAL' in jsx and 'TAPE TEXT' in jsx and 'PLACEHOLDER' not in jsx


def test_shadow_presets_scale_with_height():
    from ali.vector_rebuild.scene import scaled_shadow
    tape = scaled_shadow('tape', 83)        # accepted yellow tapes: (3,8,4,.30) (3,23,14,.40) (3,42,26,.18)
    assert [round(v) for v in tape[1][:3]] == [3, 23, 14] and tape[2][3] == .18
    ribbon = scaled_shadow('ribbon', 78)    # accepted blue ribbons: (1,4,2,.42) (2,13,7,.48) (3,25,16,.23)
    assert [round(v) for v in ribbon[2][:3]] == [3, 25, 16]


def test_word_candidates_cover_hidden_letters():
    pytest.importorskip('wordfreq')
    from ali.vector_rebuild.scene import word_candidates
    assert 'CROSS' in word_candidates('CRYOSS')
    assert 'LEVELS' in word_candidates('LLS')
    assert 'EVERY DETAIL' in word_candidates('EVERYDETAIL')
    assert word_candidates('TAPES') == []


def test_scene_refine_helpers():
    import numpy as np
    from ali.vector_rebuild import scene, scene_refine
    # a pixel on the blend between ink and plate counts as lettering, a far one does not
    ink, plate = np.array([20., 40., 30.]), np.array([250., 220., 20.])
    assert scene_refine._seg_dist((ink + plate) / 2, ink, plate) < 1
    assert scene_refine._seg_dist(np.array([240., 240., 240.]), ink, plate) > 100
    # design words win a tie only on a poster that is about design
    scene.CONTEXT['words'] = {'letter', 'type', 'design'}
    assert scene.in_context('FONT') and not scene.in_context('FOND')
    scene.CONTEXT['words'] = {'summer', 'sale'}
    assert not scene.in_context('FONT')
    # stroke cores: the quarter of the lettering farthest from the plate
    px = np.array([[60., 70., 60.]] * 30 + [[30., 45., 35.]] * 10 + [[240., 215., 20.]] * 10, np.float32)
    c = scene_refine._stroke_colour(px, plate)
    assert c[0] < 60


@pytest.mark.skipif(not LIB.is_file(), reason='Liberation Sans Bold not installed')
def test_closest_face_by_letter_shapes(tmp_path):
    """Letters cut from a line set in one face are closest to that face, whatever the spacing; a face the target
    app lacks is never a candidate."""
    from ali.vector_rebuild import glyph_match, scene
    from ali.vector_rebuild.fonts import Font, installed_names, pool_candidates
    from ali.vector_rebuild.text import rasterise, NOMINAL, fit_matrix
    faces = [Font(p) for p in (LIB, LIB.with_name('LiberationSerif-Bold.ttf'), LIB.with_name('LiberationMono-Bold.ttf')) if p.is_file()]
    if len(faces) < 2:
        pytest.skip('needs two Liberation faces')
    text = 'WORDS TOOL'
    m = fit_matrix(faces[0], text, (10, 10, 400, 60), 0, .12)    # wide letter-spacing
    G = rasterise(faces[0], text, NOMINAL, m, (80, 440), tracking=.12)
    V = np.ones_like(G)
    sg = glyph_match.source_glyphs(G, V, text)
    assert sg is not None and len(sg) == 9
    ranked = glyph_match.rank(faces, text, sg, scene.GLYPH_DISTORT_COST)
    assert ranked[0][1].postscript == faces[0].postscript and ranked[0][0] > ranked[1][0] + .05
    names = tmp_path / 'names.json'
    names.write_text(json.dumps([{'name': faces[1].postscript}]))
    assert installed_names(names) == {faces[1].postscript}
    picked = pool_candidates([str(LIB.parent)], str(names))
    assert {f.postscript for f in picked} <= {faces[1].postscript}


def test_adobe_fonts_face_is_named_not_embedded(tmp_path):
    """A free face family can be replaced by an Adobe Fonts face: the free face lays the text out, the PDF names the
    Adobe face without embedding any outlines under that name, and the font report points to Adobe Fonts."""
    import pymupdf
    from ali.vector_rebuild.fonts import Font, adobe_substitutes, ship_fonts
    from ali.vector_rebuild.output import write_pdf
    from ali.vector_rebuild.text import fit_matrix
    if not LIB.is_file():
        pytest.skip('needs Liberation Sans Bold')
    fonts = [Font(LIB)]
    spec = {'elements': [{'kind': 'plate', 'texts': [{'text': 'TAPE', 'font': fonts[0].postscript,
                                                      'font_match': {'postscript': fonts[0].postscript, 'score': .8}}]}]}
    rows = [{'postscript': 'EurostileCon-Hea', 'family': 'Eurostile Cond', 'style': 'Heavy',
             'url': 'https://fonts.adobe.com/fonts/eurostile-condensed', 'replaces_family': fonts[0].family}]
    added = adobe_substitutes(spec, fonts, rows)
    t = spec['elements'][0]['texts'][0]
    assert [f.postscript for f in added] == ['EurostileCon-Hea'] and t['font'] == 'EurostileCon-Hea'
    assert t['font_match']['source'] == 'adobe_fonts' and t['font_match']['layout_proxy'] == fonts[0].postscript
    m = fit_matrix(added[0], 'TAPE', (10, 10, 200, 40), 0, 0)
    frame = {'name': 'T', 'text': 'TAPE', 'font': 'EurostileCon-Hea', 'm': m, 'tracking': 0, 'fill': '#000000'}
    work = tmp_path / 'work'
    work.mkdir()
    write_pdf([('<svg xmlns="http://www.w3.org/2000/svg" width="220" height="60"></svg>', [frame])], fonts, (220, 60),
              tmp_path / 'scene.pdf', work)
    with pymupdf.open(tmp_path / 'scene.pdf') as d:
        assert [(f[3], f[1], f[5]) for f in d[0].get_fonts()] == [('EurostileCon-Hea', 'n/a', 'WinAnsiEncoding')]  # named, no file
        assert d[0].get_text().strip() == 'TAPE'     # character codes, not proxy glyph ids, so the real face maps them
    with pymupdf.open(work / 'layout-proxy.pdf') as d:
        assert d[0].get_fonts()[0][1] != 'n/a'                                                # cloud preview keeps the proxy
    report = ship_fonts(['EurostileCon-Hea'], fonts, tmp_path)
    assert report['adobe_fonts'][0]['url'].startswith('https://fonts.adobe.com') and not report['shipped']


def test_adobe_face_laid_out_with_measured_metrics(tmp_path):
    """An Adobe Fonts face with metrics measured in Illustrator replaces its free family as a candidate, and its
    stand-in face has exactly the measured advances and ink boxes."""
    from ali.vector_rebuild.fonts import Font, adobe_candidates
    if not LIB.is_file():
        pytest.skip('needs Liberation Sans Bold')
    free = Font(LIB)
    metrics = {'units_per_em': 1000, 'glyphs': {' ': [250], 'H': [500, 50, 0, 450, 667], 'I': [240, 40, 0, 200, 667]}}
    rows = [{'postscript': 'EurostileCon-Hea', 'replaces_family': free.family, 'metrics': metrics}]
    cands, rest = adobe_candidates(rows, [free], tmp_path)
    assert rest == [] and [f.postscript for f in cands] == ['EurostileCon-Hea']
    f = cands[0]
    assert f.covers('HI H') and not f.covers('HA')
    assert abs(f.advance('H', 1000) - 500) < 1 and abs(f.advance(' ', 1000) - 250) < 1
    x0, y0, x1, y1 = f.ink('HI', 1000, (1, 0, 0, 1, 0, 0))   # y-down page space
    assert abs(x0 - 50) <= 1 and abs(x1 - 700) <= 1 and abs(y0 + 667) <= 1 and abs(y1) <= 1


def test_accepted_preset_is_bundled():
    """The 2026-11-13 preset ships its free faces with licences and lays Eurostile Cond Heavy out from its metrics,
    under a stand-in name that does not reuse the free face's reserved name."""
    from ali.vector_rebuild.fonts import adobe_candidates, load_adobe_fonts, pool_candidates
    from ali.vector_rebuild.job import PRESET_ADOBE_FONTS, PRESET_POOL
    import tempfile
    pool = pool_candidates([str(PRESET_POOL)])
    assert {f.postscript for f in pool} == {'Anton-Regular', 'BebasNeue-Regular', 'SairaCondensed-ExtraBold'}
    assert all(PRESET_POOL.joinpath(n).is_file() for n in ('Anton.LICENSE.txt', 'BebasNeue.LICENSE.txt', 'SairaCondensed.LICENSE.txt'))
    with tempfile.TemporaryDirectory() as d:
        cands, rest = adobe_candidates(load_adobe_fonts(PRESET_ADOBE_FONTS), pool, d)
        assert rest == [] and [f.postscript for f in cands] == ['Anton-Regular', 'BebasNeue-Regular', 'EurostileCon-Hea']
        e = cands[-1]
        assert 'Saira' not in e.tt['name'].getDebugName(1) and abs(e.advance('H', 1000) - 500) < 1
