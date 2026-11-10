"""Scene graph orchestration; ali/1 ingest stays unchanged and authoritative."""
import json
from pathlib import Path
from ali.pipeline import process
from ali.runtime import atomic_json,run_engine,digest

def reconstruct(source,out,engines=('ocr',),options=None,resume=False):
 out=Path(out).resolve();source=Path(source).expanduser().resolve();options=options or {};out.mkdir(parents=True,exist_ok=True)
 manifest=out/'ingest/manifest.json'
 if manifest.exists():
  if not resume:raise FileExistsError('Reconstruction exists; use --resume')
  data=json.loads(manifest.read_text())
  if data['sha256']!=digest(source):raise ValueError('Source changed since ingest')
 else:manifest=process(source,out/'ingest',locale=options.get('locale','zh-TW'))
 data=json.loads(Path(manifest).read_text());scene={'schema':'ali-scene/1','manifest':str(manifest),'source_sha256':data['sha256'],'boards':[],'status':'running','limitations':[]};failures=[]
 for n,board in enumerate(data['artboards']):
  item={'id':board['name'],'width':board['width'],'height':board['height'],'width_pt':board.get('width_pt',board['width']),'height_pt':board.get('height_pt',board['height']),'nodes':[],'quality':{}}
  if board.get('svg'):item['nodes'].append({'id':'source-vector','type':'vector','asset':board['svg'],'provenance':'pdf_native_export' if board['source_type']=='pdf' else 'approximate_trace','visible':False,'z_index':0})
  item['nodes'].append({'id':'reference','type':'raster','asset':board['preview'],'provenance':'source_render','visible':True,'z_index':0})
  spans=json.loads(Path(board['text_metadata']).read_text()) if board.get('text_metadata') else []
  native=[s for s in spans if s.get('text','').strip()]
  if native:
   for i,s in enumerate(native):item['nodes'].append({'id':f'text-{i}','type':'text','text':s['text'],'bounds':s['bbox'],'coordinate_space':'pdf_points','font_name':s['font'],'font_size':s['size'],'confidence':None,'provenance':'pdf_text','visible':False,'z_index':100+i})
   item['quality']['ocr']='skipped: native PDF text preserved; partial scan regions not detected'
  for engine in engines:
   if engine=='ocr' and native:continue
   try:
    state=run_engine(engine,board['preview'],out/f'board-{n:03d}'/engine,options.get(engine,{}),resume=resume);result=state['result'];item['quality'][engine]=result.get('quality',{})
    if engine=='ocr':
     for i,text in enumerate(result['records']):item['nodes'].append(dict(text,id=f'ocr-{i}',type='text',coordinate_space='pixels',visible=False,z_index=100+i))
    elif engine=='segmentation':
     next(n for n in item['nodes'] if n['id']=='reference')['visible']=False
     item['nodes'].append(dict(result['background'],id='background',type='raster',asset=result['background']['image'],visible=True,z_index=0))
     for layer in result['layers']:item['nodes'].append(dict(layer,type='raster',asset=layer['image'],visible=True))
    elif engine=='sr':item['nodes'].append({'id':'sr4x','type':'raster','asset':result['image'],'provenance':'real_esrgan_generated','visible':False,'z_index':0})
   except Exception as exc:failures.append({'board':board['name'],'engine':engine,'error':str(exc)})
  scene['boards'].append(item);atomic_json(out/'scene.json',scene)
 scene['status']='partial' if failures else 'complete';scene['failures']=failures;scene['limitations']=['Text nodes are hidden until human font/layout approval; source raster remains reference','Inferred background/order are not original design data','Native Adobe success requires app save and reopen verification'];atomic_json(out/'scene.json',scene)
 atomic_json(out/'quality-report.json',{'status':scene['status'],'failures':failures,'boards':[b['quality'] for b in scene['boards']],'native_adobe_verified':False})
 return scene
