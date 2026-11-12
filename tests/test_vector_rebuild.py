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
