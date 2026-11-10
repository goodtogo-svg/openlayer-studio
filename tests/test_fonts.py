import json
from ali.fonts import compare_fonts, _key, report_for_manifest

def test_pdf_subset_and_free_fallback():
    inventory=[{'family':'Noto Sans TC','full_name':'Noto Sans TC Regular','file':'/tmp/Noto.ttf','face_index':0}]
    report=compare_fonts(['ABCDEF+NotoSansTC','UnknownFont'],'zh-TW',inventory)
    assert report['source_fonts'][0]['installed_exact_name'] is True
    assert report['source_fonts'][1]['installed_exact_name'] is False
    assert report['free_font_recommendations'][0]['installed'] is True
    assert report['source_fonts'][0]['identity_verified'] is False

def test_metadata_report(tmp_path,monkeypatch):
    from ali import fonts
    monkeypatch.setattr(fonts,'list_local_fonts',lambda extra_dirs=(): [])
    spans=tmp_path/'spans.json';spans.write_text(json.dumps([{'font':'ABCDEFG+Helvetica','text':'hello'}]))
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps({'settings':{'locale':'en'},'artboards':[{'text_metadata':str(spans)}]}))
    report=report_for_manifest(manifest,tmp_path/'font_report.json')
    assert report['source_fonts'][0]['source_font']=='ABCDEFG+Helvetica'
    assert (tmp_path/'font_report.json').exists()
