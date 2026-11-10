import json
from pathlib import Path
import pymupdf
from ali.pdf_rebuild import prepare_rebuild, classify_spans


def test_unknown_fonts_and_shadow_duplicates_keep_original():
    common={'font':'Known','size':12,'origin':[10,20],'bbox':[10,8,40,22],'alpha':255,'color':0,'direction':[1,0]}
    spans=[dict(common,text='body'),dict(common,text='shadow',bbox=[10,30,50,44]),dict(common,text='shadow',bbox=[11,31,51,45]),dict(common,text='special',font='Unknown',bbox=[10,60,50,74])]
    editable,preserved=classify_spans(spans,{'Known':{'adobe_name':'Known\tRegular'}})
    assert [x['text'] for x in editable]==['body']
    assert len(preserved)==3
    assert {x['preserve_reason'] for x in preserved}=={'overlapping_text_effect','unmapped_font'}


def test_rebuild_removes_only_editable_text_preserves_graphics(tmp_path):
    source=tmp_path/'source.pdf';doc=pymupdf.open();page=doc.new_page(width=300,height=200)
    page.draw_rect((5,5,295,195),color=(1,0,0));page.insert_text((20,40),'Editable',fontname='helv');page.insert_text((20,80),'Preserved',fontname='cour');doc.save(source);doc.close()
    before=source.read_bytes();plan=prepare_rebuild(source,tmp_path/'job',{'Helvetica':{'adobe_name':'Helvetica\tRegular'}})
    assert plan['pages'][0]['editable_count']==1
    bg=pymupdf.open(plan['background_pdf']);assert 'Editable' not in bg[0].get_text() and 'Preserved' in bg[0].get_text()
    assert len(bg[0].get_drawings())==1
    assert bg[0].rect==pymupdf.Rect(0,0,300,200)
    bg.close();assert source.read_bytes()==before
    assert Path(plan['script']).exists()
    assert not Path(plan['expected_indd']).exists()


def test_preserved_glyph_overlap_prevents_unsafe_removal():
    spans=[{'font':'Known','text':'native','bbox':[0,0,20,20],'origin':[0,15]}, {'font':'Special','text':'effect','bbox':[10,10,30,30],'origin':[10,25]}]
    editable,preserved=classify_spans(spans,{'Known':{'adobe_name':'Known'}})
    assert not editable and len(preserved)==2
    assert preserved[-1]['preserve_reason']=='overlaps_preserved_text'


def test_mixed_page_sizes_are_not_silently_resized(tmp_path):
    import pytest
    source=tmp_path/'mixed.pdf';doc=pymupdf.open();doc.new_page(width=100,height=200);doc.new_page(width=200,height=200);doc.save(source);doc.close()
    with pytest.raises(ValueError,match='Mixed page dimensions'):prepare_rebuild(source,tmp_path/'job',{})
