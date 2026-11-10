import json
from pathlib import Path
import pymupdf
from ali.article_flow import paragraphize, build_article_plan
from ali.pdf_rebuild import prepare_rebuild


def span(id,text,x,y):
    return {'id':id,'text':text,'origin':[x,y],'bbox':[x,y-8,x+30,y+2],'font':'Helvetica','size':10,'color':0}


def test_column_continuation_is_one_paragraph_without_forced_line_breaks():
    columns=[{'rect':[0,0,80,80],'spans':[span('a','跨欄文章',20,20),span('b','持續內容',0,35)]},{'rect':[100,0,180,80],'spans':[span('c','進入第二欄',100,20)]}]
    paragraphs=paragraphize(columns)
    assert len(paragraphs)==1
    assert paragraphs[0]['text']=='跨欄文章持續內容進入第二欄'
    assert paragraphs[0]['source_ids']==['a','b','c']
    assert '\n' not in paragraphs[0]['text'] and '\r' not in paragraphs[0]['text']


def test_paragraph_indent_is_a_paragraph_boundary_not_every_line():
    paragraphs=paragraphize([{'rect':[0,0,80,100],'spans':[span('a','第一段',20,20),span('b','第一段續',0,35),span('c','第二段',20,50),span('d','第二段續',0,65)]}])
    assert [p['text'] for p in paragraphs]==['第一段第一段續','第二段第二段續']


def test_latin_wrap_restores_word_separator():
    paragraphs=paragraphize([{'rect':[0,0,100,100],'spans':[span('a','Learn with',0,20),span('b','confidence.',0,35)]}],single=True)
    assert paragraphs[0]['text']=='Learn with confidence.'


def test_explicit_articles_have_separate_stories_and_threaded_columns(tmp_path):
    source=tmp_path/'source.pdf';d=pymupdf.open();p=d.new_page(width=300,height=200)
    p.insert_text((10,20),'Alpha begins',fontsize=8);p.insert_text((10,35),'and continues',fontsize=8);p.insert_text((110,20),'in second column.',fontsize=8);p.insert_text((10,140),'Separate article.',fontsize=8);d.save(source);d.close()
    base=prepare_rebuild(source,tmp_path/'base',{'Helvetica':{'adobe_name':'Helvetica\tRegular'}})
    layout=tmp_path/'layout.json';layout.write_text(json.dumps({'schema':'ali-article-layout/1','pages':[{'articles':[{'id':'first','columns':[[9,0,100,90],[109,0,210,90]]},{'id':'second','columns':[[9,120,210,180]]}]}]}))
    plan=build_article_plan(tmp_path/'base/rebuild-plan.json',layout,tmp_path/'flow')
    groups=plan['pages'][0]['groups']
    assert len(groups)==2 and [len(g['frames']) for g in groups]==[2,1]
    assert len(groups[0]['paragraphs'])==1
    assert groups[0]['paragraphs'][0]['text']=='Alpha begins and continues in second column.'
    assert groups[1]['paragraphs'][0]['text']=='Separate article.'
    assert len([i for g in groups for p in g['paragraphs'] for i in p['source_ids']])==4
    assert not Path(plan['indd']).exists()


def test_overlapping_article_ownership_is_rejected(tmp_path):
    import pytest
    from ali.runtime import digest
    source=tmp_path/'source.pdf';d=pymupdf.open();p=d.new_page(width=200,height=100);p.insert_text((20,30),'Owned once');d.save(source);d.close()
    layout=tmp_path/'layout.json';layout.write_text(json.dumps({'schema':'ali-article-layout/1','pages':[{'articles':[{'id':'a','columns':[[10,10,150,80]]},{'id':'b','columns':[[10,10,150,80]]}]}]}))
    with pytest.raises(ValueError,match='overlapping article'):
        build_article_plan({'source':str(source),'source_sha256':digest(source),'font_map':{'Helvetica':{'adobe_name':'Helvetica'}}},layout,tmp_path/'out')
