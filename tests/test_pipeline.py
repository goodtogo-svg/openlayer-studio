import json
from pathlib import Path
from PIL import Image
from ali.pipeline import process,validate
from ali.adobe import generate

def test_png_end_to_end(tmp_path):
    inp=tmp_path/'input.png';Image.new('RGBA',(64,48),(10,40,100,200)).save(inp)
    out=tmp_path/'output';manifest=process(inp,out)
    assert validate(manifest)['ok']
    data=json.loads(manifest.read_text())
    assert data['artboards'][0]['width']==64
    assert data['sha256']
    for app in ('photoshop','illustrator','indesign'):
        jobs=generate(manifest,app,tmp_path/app)
        if app=='illustrator' and not data['artboards'][0].get('svg'):
            assert jobs == []  # optional vector dependency unavailable in this test runtime
            continue
        assert jobs and Path(jobs[0]['script']).is_file()
        assert jobs[0]['status']=='requires_adobe_execution'
        assert not Path(jobs[0]['expected_output']).exists()

def test_bad_input(tmp_path):
    import pytest
    with pytest.raises(ValueError):process(tmp_path/'missing.jpg',tmp_path/'out')

def test_validate_missing_layer(tmp_path):
    p=tmp_path/'image.png';Image.new('RGBA',(4,4)).save(p)
    manifest=process(p,tmp_path/'out',vectorize=False)
    data=json.loads(manifest.read_text());data['artboards'][0]['layers'][0]['image']=str(tmp_path/'missing.png');manifest.write_text(json.dumps(data))
    assert not validate(manifest)['ok']

def test_pdf_extract(tmp_path):
    import pymupdf
    doc=pymupdf.open();pg=doc.new_page(width=100,height=100);pg.insert_text((10,30),'Test PDF')
    inp=tmp_path/'in.pdf';doc.save(inp);doc.close()
    manifest=process(inp,tmp_path/'out')
    data=json.loads(manifest.read_text());assert data['artboards'][0]['svg'];assert validate(manifest)['ok']

def test_multilingual_locale_and_script_audit(tmp_path):
    from ali.language import audit_text
    p=tmp_path/'test.png';Image.new('RGB',(10,10)).save(p)
    manifest=process(p,tmp_path/'out',locale='zh-TW',vectorize=False)
    data=json.loads(manifest.read_text(encoding='utf-8'))
    assert data['settings']['locale']=='zh-TW'
    audit=audit_text([{'text':'繁體中文 English 日本語 한글 العربية'}])
    assert audit['scripts']['Han (shared by Chinese/Japanese)']>0
    assert audit['has_rtl_script']

def test_unsupported_locale(tmp_path):
    import pytest
    p=tmp_path/'test.png';Image.new('RGB',(10,10)).save(p)
    with pytest.raises(ValueError):process(p,tmp_path/'out',locale='xx')


def test_pdf_indesign_preserves_physical_size_across_dpi(tmp_path):
    import pymupdf
    import re
    source = tmp_path / 'fractional.pdf'
    doc = pymupdf.open()
    doc.new_page(width=594.96, height=841.92)
    doc.save(source)
    doc.close()
    for dpi in (72, 144, 300):
        manifest = process(source, tmp_path / str(dpi), dpi=dpi)
        data = json.loads(manifest.read_text())
        board = data['artboards'][0]
        assert abs(board['width_pt'] - 594.96) < 0.001
        assert abs(board['height_pt'] - 841.92) < 0.001
        job = generate(manifest, 'indesign', tmp_path / f'adobe-{dpi}')[0]
        script = Path(job['script']).read_text()
        width = float(re.search(r'pageWidth="([\d.]+)pt"', script)[1])
        height = float(re.search(r'pageHeight="([\d.]+)pt"', script)[1])
        assert abs(width - 594.96) < 0.001
        assert abs(height - 841.92) < 0.001
        assert not Path(job['expected_output']).exists()
