import io
import json
import shutil
import subprocess
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from ali.cli import main
from ali.pdf_to_ai import compile_pdf, drawing_subpaths, verify_job, _Space, _split_runs

DEJAVU = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')


def _fixture(path, embed_font=False):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    page.draw_rect((10, 10, 100, 60), color=(1, 0, 0), fill=(0, 0, 1), width=2)
    page.draw_bezier((10, 100), (50, 50), (90, 150), (130, 100), color=(0, .5, 0), width=3)
    shape = page.new_shape()
    shape.draw_circle((200, 100), 30)
    shape.draw_circle((200, 100), 15)
    shape.finish(fill=(1, .8, 0), even_odd=True)
    shape.commit()
    page.draw_rect((250, 150, 330, 210), fill=(0, 1, 0), color=None, fill_opacity=0.5)
    buffer = io.BytesIO()
    Image.linear_gradient('L').convert('RGB').resize((64, 32)).save(buffer, 'PNG')
    page.insert_image((260, 20, 380, 80), stream=buffer.getvalue())
    if embed_font:
        page.insert_font(fontname='dv', fontfile=str(DEJAVU))
        page.insert_text((20, 200), 'Hello compiler world', fontname='dv', fontsize=18, color=(.2, .2, .6))
        page.insert_text((350, 280), 'Rotated', fontname='dv', fontsize=14, rotate=90)
    else:
        page.insert_text((20, 200), 'Hello world', fontname='helv', fontsize=18)
    second = doc.new_page(width=200, height=300)
    second.draw_rect((10, 10, 50, 30), fill=(1, 0, 0), color=None)
    doc.subset_fonts()
    doc.save(path)
    doc.close()


def test_rect_and_curves_become_illustrator_paths():
    page = pymupdf.open().new_page(width=100, height=50)
    space = _Space(page, 10)
    items = [('l', pymupdf.Point(0, 0), pymupdf.Point(10, 0)),
             ('c', pymupdf.Point(10, 0), pymupdf.Point(12, 5), pymupdf.Point(8, 9), pymupdf.Point(0, 0)),
             ('re', pymupdf.Rect(20, 20, 30, 40), 1)]
    first, rect = drawing_subpaths(items, space)
    assert first['closed'] and len(first['points']) == 2      # returning to the start closes the subpath
    assert first['points'][0][:2] == [10.0, 0.0]               # x shifted to the artboard, y flipped
    assert first['points'][1][4:6] == [22.0, -5.0]             # outgoing handle of the curve
    assert first['points'][0][2:4] == [18.0, -9.0]             # incoming handle merged into the start point
    assert rect['closed'] and [p[:2] for p in rect['points']] == [[30, -20], [40, -20], [40, -40], [30, -40]]


def test_rotated_page_maps_to_visual_coordinates():
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    page.set_rotation(90)
    space = _Space(page, 0)
    assert space.point(10, 10) == [190.0, -10.0]


def test_word_spaces_are_restored_from_glyph_positions():
    chars = [(72, 1, (0, 10), (0, 0, 8, 10)), (105, 2, (8, 10), (8, 0, 12, 10)),
             (121, 3, (20, 10), (20, 0, 28, 10)), (111, 4, (28, 10), (28, 0, 36, 10)),
             (78, 5, (0, 30), (0, 20, 8, 30))]
    runs = _split_runs({'dir': (1, 0), 'size': 10, 'spacewidth': 3, 'chars': chars})
    assert [r['text'] for r in runs] == ['Hi yo', 'N']


def test_compile_writes_job_without_touching_source(tmp_path):
    source = tmp_path / 'in.pdf'
    _fixture(source)
    before = source.read_bytes()
    result = compile_pdf(source, tmp_path / 'job', engine='ir')
    assert source.read_bytes() == before
    plan = json.loads(Path(result['ir']).read_text())
    assert plan['schema'] == 'ali-pdf-ai/1' and plan['status'] == 'requires_illustrator_execution'
    assert [p['artboard'] for p in plan['pages']] == [[0.0, 0.0, 400.0, -300.0], [436.0, 0.0, 636.0, -300.0]]
    first = plan['pages'][0]['items']
    kinds = [i['kind'] for i in first]
    assert kinds.count('image') == 1 and kinds.count('text') == 1
    assert first == sorted(first, key=lambda i: i['seqno'])
    ring = [i for i in first if i['kind'] == 'path' and i['even_odd']][0]
    assert len(ring['subpaths']) == 2
    translucent = [i for i in first if i['kind'] == 'path' and i['opacity'] == 0.5]
    assert len(translucent) == 1
    image = [i for i in first if i['kind'] == 'image'][0]
    assert (Path(result['job']) / image['file']).exists()
    assert image['center'] == [320.0, -50.0] and image['width'] == 120.0 and not image['mirrored']
    text = [i for i in first if i['kind'] == 'text'][0]
    assert text['text'] == 'Hello world' and text['font'] == 'Helvetica' and text['origin'] == [20.0, -200.0]
    assert text['outline'] is None and text['outline_blocker'] == 'font_not_embedded_or_unsupported'
    assert Path(result['script']).read_text().startswith('#target illustrator')
    assert not Path(result['expected_ai']).exists()
    with pytest.raises(FileExistsError):
        compile_pdf(source, tmp_path / 'job')


@pytest.mark.skipif(not DEJAVU.exists(), reason='DejaVu Sans not installed')
def test_embedded_font_outlines_match_source(tmp_path):
    pytest.importorskip('fontTools')
    source = tmp_path / 'in.pdf'
    _fixture(source, embed_font=True)
    result = compile_pdf(source, tmp_path / 'job', engine='ir')
    texts = [i for p in json.loads(Path(result['ir']).read_text())['pages'] for i in p['items'] if i['kind'] == 'text']
    assert [t['text'] for t in texts] == ['Hello compiler world', 'Rotated']
    assert texts[0]['font'] == 'DejaVuSans'
    assert all(t['outline'] for t in texts) and texts[1]['matrix'] == [0, 1, -1, 0]
    assert {t['geometry'] for t in texts} == {'exact'}
    # The IR redrawn without Illustrator must match the source almost pixel for pixel.
    assert max(result['front_end_preview']['pixels_over_32_per_page']) < 0.005


def test_outline_mode_and_page_selection(tmp_path):
    source = tmp_path / 'in.pdf'
    _fixture(source)
    result = compile_pdf(source, tmp_path / 'job', text_mode='outline', pages='2', engine='ir')
    assert result['pages'] == 1 and result['summary']['text_runs'] == 0
    with pytest.raises(ValueError, match='out of range'):
        compile_pdf(source, tmp_path / 'job2', pages='3')


def test_output_inside_source_folder_is_rejected(tmp_path):
    source = tmp_path / 'in.pdf'
    _fixture(source)
    with pytest.raises(ValueError):
        compile_pdf(source, tmp_path)


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_generated_extendscript_parses(tmp_path):
    source = tmp_path / 'in.pdf'
    _fixture(source)
    result = compile_pdf(source, tmp_path / 'job', engine='ir')
    for script in (result['script'], result['verify_script']):
        body = Path(script).read_text().replace('#target illustrator', '', 1)
        check = "new Function(require('fs').readFileSync(0,'utf8'))"
        subprocess.run(['node', '-e', check], input=body, text=True, check=True)


def test_verify_requires_illustrator_evidence_then_compares_pixels(tmp_path):
    source = tmp_path / 'in.pdf'
    _fixture(source)
    result = compile_pdf(source, tmp_path / 'job', engine='ir')
    job = Path(result['job'])
    assert verify_job(job)['status'] == 'requires_illustrator_execution'
    plan = json.loads((job / 'compile-ir.json').read_text())
    (job / plan['output']['build_report']).write_text(json.dumps({'complete': True, 'errors': []}))
    (job / plan['output']['verification']).write_text(json.dumps({'saved_and_reopened': True}))
    with pymupdf.open(source) as doc:
        for n, page in enumerate(doc):
            page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).save(job / ('proof-artboard-%d.png' % (n + 1)))
    verified = verify_job(job)
    assert verified['status'] == 'candidate_pass' and verified['source_sha256_unchanged']
    assert all(p['pixels_over_32'] == 0 for p in verified['pages'])


def test_cli_pdf_ai(tmp_path, capsys):
    source = tmp_path / 'in.pdf'
    _fixture(source)
    mapping = tmp_path / 'map.json'
    mapping.write_text(json.dumps({'Helvetica': 'ArialMT'}))
    assert main(['pdf-ai', str(source), '--out', str(tmp_path / 'job'), '--font-map', str(mapping)]) == 0
    printed = json.loads(capsys.readouterr().out)
    plan = json.loads(Path(printed['ir']).read_text())
    text = [i for i in plan['pages'][0]['items'] if i['kind'] == 'text'][0]
    assert text['font'] == 'ArialMT' and text['font_mapped']
    assert main(['pdf-ai-verify', printed['job']]) == 2


def test_import_engine_copies_source_and_keeps_only_text(tmp_path):
    source = tmp_path / 'in.pdf'
    _fixture(source)
    result = compile_pdf(source, tmp_path / 'job')
    plan = json.loads(Path(result['ir']).read_text())
    assert plan['engine'] == 'illustrator-import' and plan['source_copy'] == 'source.pdf'
    assert (Path(result['job']) / 'source.pdf').read_bytes() == source.read_bytes()
    assert {i['kind'] for p in plan['pages'] for i in p['items']} == {'text'}
    text = plan['pages'][0]['items'][0]
    left, top, right, bottom = text['bounds']
    assert left <= text['origin'][0] < right and bottom < text['origin'][1] <= top
    assert result['front_end_preview'] is None
    with pytest.raises(ValueError, match='keep'):
        compile_pdf(source, tmp_path / 'job2', text_mode='keep', engine='ir')
