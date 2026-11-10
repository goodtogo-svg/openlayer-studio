from pathlib import Path
import io
import numpy as np
import pytest
pikepdf=pytest.importorskip("pikepdf",reason="Install .[graphics] for PDF graphic-object tests")
import pymupdf as fitz
from PIL import Image
from ali.graphics import scan_page,write_overlay,native_path


def make_image_pdf(path):
    im=Image.new('RGB',(120,120),'#dc4520');raw=io.BytesIO();im.save(raw,format='PNG')
    d=fitz.open();p=d.new_page(width=120,height=100);p.draw_rect(p.rect,fill=(.2,.5,.8),color=None);p.insert_image(fitz.Rect(20,10,100,90),stream=raw.getvalue());d.save(path);d.close()


def test_image_scan_and_native_clip_curve(tmp_path):
    source=tmp_path/'source.pdf';make_image_pdf(source)
    with pikepdf.Pdf.open(source) as pdf:
        ops,records,m=scan_page(pdf,0);images=[r for r in records if r['kind']=='image'];assert len(images)==1
        assert np.allclose(images[0]['box'],[20,10,100,90])
    circle=[([20,50],'m'),([20,72.0914,37.9086,90,60,90],'c'),([82.0914,90,100,72.0914,100,50],'c'),([100,27.9086,82.0914,10,60,10],'c'),([37.9086,10,20,27.9086,20,50],'c'),([],'h')]
    anchors=native_path(circle,fitz.Matrix(1,0,0,-1,0,100));assert len(anchors)==4
    assert anchors[0][0]!=anchors[0][1] and anchors[0][2]!=anchors[0][1]
    assert native_path(circle[:-1],fitz.Matrix(1,0,0,-1,0,100))==anchors


def test_paint_overlay_composes_exactly_and_base_has_no_duplicate_image(tmp_path):
    source=tmp_path/'source.pdf';make_image_pdf(source);before=source.read_bytes()
    with pikepdf.Pdf.open(source) as pdf:
        ops,records,m=scan_page(pdf,0);image=next(r for r in records if r['kind']=='image')
        write_overlay(pdf,0,[image],image['visible_pdf'],tmp_path/'photo.pdf')
        remaining=[op for i,op in enumerate(ops) if i!=image['index']]
        pdf.pages[0].obj.Contents=pdf.make_stream(pikepdf.unparse_content_stream(remaining));pdf.pages[0].remove_unreferenced_resources();pdf.save(tmp_path/'base.pdf')
    with pikepdf.Pdf.open(tmp_path/'base.pdf') as pdf:
        assert not [r for r in scan_page(pdf,0)[1] if r['kind']=='image']
    a=fitz.open(source);b=fitz.open(tmp_path/'base.pdf');overlay=fitz.open(tmp_path/'photo.pdf');b[0].show_pdf_page(fitz.Rect(image['box']),overlay,0)
    x=a[0].get_pixmap(alpha=False);y=b[0].get_pixmap(alpha=False)
    assert x.samples==y.samples
    a.close();b.close();overlay.close();assert source.read_bytes()==before


def test_original_soft_mask_becomes_png_alpha_without_ai(tmp_path):
    from ali.graphics import _rgba_image
    im=Image.new('RGBA',(2,2));im.putdata([(255,0,0,0),(255,0,0,85),(255,0,0,170),(255,0,0,255)])
    raw=io.BytesIO();im.save(raw,format='PNG');source=tmp_path/'alpha.pdf';d=fitz.open();page=d.new_page(width=100,height=100);page.insert_image((10,10,90,90),stream=raw.getvalue());d.save(source);d.close()
    with pikepdf.Pdf.open(source) as pdf:
        image=next(r for r in scan_page(pdf,0)[1] if r['kind']=='image')
        asset=_rgba_image(image['object'],tmp_path/'original-alpha')
    with Image.open(asset) as result:assert list(result.getchannel('A').getdata())==[0,85,170,255]


def test_clip_transform_tracks_q_and_q_restore(tmp_path):
    source=tmp_path/'source.pdf';make_image_pdf(source)
    with pikepdf.Pdf.open(source) as pdf:
        _,records,_=scan_page(pdf,0);image=next(r for r in records if r['kind']=='image')
        assert list(image['matrix'])==[80,0,0,80,20,10]


def test_equivalent_clip_boxes_prefer_curve_over_rectangle():
    import pymupdf as fitz
    from ali.graphics import select_clip
    rectangle={'box':fitz.Rect(0,0,60,60),'path':[([], 'l')]}
    curve={'box':fitz.Rect(-.000004,0,60,60),'path':[([], 'c')]}
    assert select_clip([rectangle,curve]) is curve
