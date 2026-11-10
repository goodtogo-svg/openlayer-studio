import json
from pathlib import Path
import pytest
from PIL import Image
from ali.runtime import atomic_json,digest,run_engine
from ali.reconstruct import reconstruct
from ali.scene_adobe import generate_scene
from ali.engines.segmentation import validate_prompts


def test_prompt_coordinate_contract():
 assert validate_prompts({'schema':'ali-prompts/1','objects':[{'points':[[1,2]],'labels':[1],'box':[0,0,8,8]}]},10,10)
 with pytest.raises(ValueError):validate_prompts({'schema':'ali-prompts/1','objects':[{'points':[[99,2]],'labels':[1]}]},10,10)
 with pytest.raises(ValueError):validate_prompts({'schema':'ali-prompts/1','objects':[{'points':[[1,2]],'labels':[]}]},10,10)


def test_missing_environment_records_failure_and_source_unchanged(tmp_path,monkeypatch):
 import ali.runtime as runtime
 monkeypatch.setattr(runtime,'ROOT',tmp_path/'missing')
 source=tmp_path/'a.png';Image.new('RGB',(8,8)).save(source);sha=digest(source)
 with pytest.raises(RuntimeError,match='Missing isolated environment'):run_engine('sr',source,tmp_path/'job')
 state=json.loads((tmp_path/'job/state.json').read_text())
 assert state['status']=='failed' and digest(source)==sha
 assert not (tmp_path/'job/result.json').exists()
 assert not (tmp_path/'job/.running').exists()


def test_resume_invalidates_tampered_artifact(tmp_path,monkeypatch):
 import ali.runtime as runtime
 monkeypatch.setattr(runtime,'ROOT',tmp_path/'missing')
 source=tmp_path/'source.png';Image.new('RGB',(8,8)).save(source)
 with pytest.raises(RuntimeError):run_engine('sr',source,tmp_path/'job')
 state=json.loads((tmp_path/'job/state.json').read_text());artifact=tmp_path/'job/output.png';artifact.write_bytes(b'old')
 state.update(status='complete',artifacts={str(artifact):digest(artifact)});atomic_json(tmp_path/'job/state.json',state)
 assert run_engine('sr',source,tmp_path/'job',resume=True)['status']=='complete'
 artifact.write_bytes(b'changed')
 with pytest.raises(RuntimeError):run_engine('sr',source,tmp_path/'job',resume=True)
 assert json.loads((tmp_path/'job/state.json').read_text())['status']=='failed'


def test_changed_resume_options_rejected(tmp_path,monkeypatch):
 import ali.runtime as runtime
 monkeypatch.setattr(runtime,'ROOT',tmp_path/'missing');source=tmp_path/'a.png';source.write_bytes(b'source')
 with pytest.raises(RuntimeError):run_engine('ocr',source,tmp_path/'job',{'language':'en'})
 with pytest.raises(ValueError,match='changed'):run_engine('ocr',source,tmp_path/'job',{'language':'ja'},resume=True)


def test_native_pdf_text_bypasses_ocr(tmp_path,monkeypatch):
 import pymupdf,ali.reconstruct as module
 src=tmp_path/'native.pdf';d=pymupdf.open();p=d.new_page(width=100,height=100);p.insert_text((10,20),'Native');d.save(src);d.close()
 def forbidden(*args,**kwargs):raise AssertionError('OCR must not run')
 monkeypatch.setattr(module,'run_engine',forbidden)
 scene=reconstruct(src,tmp_path/'job')
 assert scene['status']=='complete'
 assert any(n.get('text')=='Native' and n['provenance']=='pdf_text' for n in scene['boards'][0]['nodes'])
 assert json.loads(Path(scene['manifest']).read_text())['schema']=='ali/1'


def test_missing_engine_makes_partial_scene(tmp_path,monkeypatch):
 import ali.reconstruct as module
 src=tmp_path/'a.png';Image.new('RGB',(8,8)).save(src)
 def failed(*args,**kwargs):raise RuntimeError('Model missing')
 monkeypatch.setattr(module,'run_engine',failed)
 scene=reconstruct(src,tmp_path/'out',('segmentation',))
 assert scene['status']=='partial' and scene['failures'][0]['engine']=='segmentation'


def test_scene_text_requires_explicit_font_approval(tmp_path):
 scene={'schema':'ali-scene/1','boards':[{'width':100,'height':100,'width_pt':100,'height_pt':100,'nodes':[{'id':'t','type':'text','text':'hello','visible':True,'bounds':[0,0,20,20]}]}]}
 p=tmp_path/'scene.json';atomic_json(p,scene)
 with pytest.raises(ValueError,match='human_approved'):generate_scene(p,'indesign',tmp_path/'adobe')
 assert not list((tmp_path/'adobe').glob('*.indd'))


def test_tile_blending_dimensions_and_edges():
 import numpy as np
 from ali.engines.sr import blend_tiles
 src=np.arange(31*29*3,dtype=np.uint8).reshape(31,29,3)
 def identity4(x):return np.repeat(np.repeat(x,4,axis=0),4,axis=1)
 assert np.array_equal(blend_tiles(src,identity4,tile=16,overlap=3),identity4(src))
 with pytest.raises(ValueError):blend_tiles(src,identity4,tile=16,overlap=9)


def test_ocr_polygon_angle_and_confidence():
 from ali.engines.ocr import normalize_result
 rows=normalize_result({'rec_texts':['繁體中文'],'rec_scores':[.91],'rec_polys':[[[0,0],[10,10],[8,12],[-2,2]]]},'zh-TW')
 assert rows[0]['text']=='繁體中文' and rows[0]['angle_degrees']==45
 assert rows[0]['language_provenance']=='requested_recognizer_not_language_detection'


def test_checkpoint_integrity_rejects_tampering(tmp_path,monkeypatch):
 import ali.runtime as runtime
 monkeypatch.setattr(runtime,'ROOT',tmp_path);spec=runtime.REGISTRY['sam2-tiny'];p=tmp_path/'models/weights'/spec['file'];p.parent.mkdir(parents=True);p.write_bytes(b'weights');atomic_json(p.with_suffix(p.suffix+'.json'),{'sha256':digest(p),'url':spec['url']})
 assert runtime.model_status('sam2-tiny')['ready']
 p.write_bytes(b'bad')
 assert not runtime.model_status('sam2-tiny')['ready']


def test_segmentation_auto_backend_falls_back_explicitly(monkeypatch):
    import sys, types
    from ali.engines import segmentation, common
    calls=[]
    def model(request):
        calls.append(request['options']['backend'])
        if len(calls)==1:raise NotImplementedError('MPS unsupported operation')
        return {'backend':'cpu'}
    monkeypatch.setattr(segmentation,'_infer',model)
    monkeypatch.setattr(common,'torch_backend',lambda _: 'mps')
    monkeypatch.setitem(sys.modules,'torch',types.SimpleNamespace(mps=types.SimpleNamespace(empty_cache=lambda:None)))
    result=segmentation.infer({'options':{'backend':'auto'}})
    assert calls==['auto','cpu'] and 'MPS unsupported' in result['cpu_fallback_reason']


def test_font_render_common_baseline():
    from ali.engines.fonts import render, ink, shape
    import numpy as np
    font={'path':'models/fonts/Inter-400.ttf','face_index':0}
    if not Path(font['path']).exists():pytest.skip('Optional open font fixture not provisioned')
    rendered=render('Adobe Layer 2026',font,36,0)
    reference=Image.new('L',rendered.size,255)
    from PIL import ImageDraw,ImageFont
    ImageDraw.Draw(reference).text((36,72),'Adobe Layer 2026',font=ImageFont.truetype(font['path'],36),fill=0,anchor='ls')
    a=shape(ink(rendered));b=shape(ink(reference));assert (a&b).sum()/(a|b).sum()>.9


def test_photoshop_visible_vector_is_not_silently_discarded(tmp_path):
    scene={'schema':'ali-scene/1','boards':[{'width':100,'height':100,'width_pt':100,'height_pt':100,'nodes':[{'id':'v','type':'vector','visible':True,'asset':'missing.svg'}]}]}
    p=tmp_path/'scene.json';atomic_json(p,scene)
    with pytest.raises(ValueError,match='raster derivative'):generate_scene(p,'photoshop',tmp_path/'out')


def test_photoshop_scene_preserves_physical_resolution(tmp_path):
    import re
    scene={'schema':'ali-scene/1','boards':[{'width':264,'height':238,'width_pt':132,'height_pt':119,'nodes':[]}]}
    source=tmp_path/'scene.json';atomic_json(source,scene)
    job=generate_scene(source,'photoshop',tmp_path/'ps')[0]
    script=Path(job['script']).read_text()
    resolution=float(re.search(r'UnitValue\(238,"px"\),(\d+(?:\.\d+)?),',script)[1])
    assert resolution==144
    assert 264/resolution*72==132 and 238/resolution*72==119


def test_photoshop_scene_rejects_non_square_pixel_geometry(tmp_path):
    scene={'schema':'ali-scene/1','boards':[{'width':264,'height':238,'width_pt':132,'height_pt':200,'nodes':[]}]}
    source=tmp_path/'scene.json';atomic_json(source,scene)
    with pytest.raises(ValueError,match='pixel aspect'):generate_scene(source,'photoshop',tmp_path/'ps')
