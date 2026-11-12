import json
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
