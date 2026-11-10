"""PaddleOCR 3.x: explicit downloads, polygon masks and conservative language metadata."""
from pathlib import Path
import json,math
import numpy as np
from PIL import Image,ImageDraw
from ali.runtime import ROOT,atomic_json,digest
from .common import load_image
LANGS={'zh-TW':'chinese_cht','zh-CN':'ch','en':'en','ja':'japan','ko':'korean'}

def normalize_result(data,language):
 records=[]
 polygons=data.get('rec_polys',data.get('dt_polys',[]))
 for text,score,poly in zip(data.get('rec_texts',[]),data.get('rec_scores',[]),polygons):
  points=np.asarray(poly).tolist();dx=points[1][0]-points[0][0];dy=points[1][1]-points[0][1]
  records.append({'text':text,'polygon':points,'angle_degrees':math.degrees(math.atan2(dy,dx)),'confidence':float(score),'language':language,'language_provenance':'requested_recognizer_not_language_detection','provenance':'paddleocr'})
 return records

def infer(req):
 opts=req['options'];language=opts.get('language','zh-TW')
 if language not in LANGS:raise ValueError('Unsupported OCR language')
 from paddleocr import PaddleOCR
 from importlib.metadata import version
 out=Path(req['out']);im=load_image(req['source']);receipt=ROOT/'models/ocr'/f'{language}.json'
 if not receipt.exists() and not opts.get('download_models'):raise RuntimeError('OCR models missing; rerun with explicit --download-models')
 if receipt.exists() and not opts.get('download_models'):
  meta=json.loads(receipt.read_text())
  if not all(Path(p).is_file() and digest(p)==sha for p,sha in meta['files'].items()):raise RuntimeError('OCR cache changed or missing; explicitly reprovision models')
 rec_name={'en':'en_PP-OCRv5_mobile_rec','ko':'korean_PP-OCRv5_mobile_rec'}.get(language,'PP-OCRv5_server_rec')
 det_name='PP-OCRv5_server_det';cache=ROOT/'models/paddlex/official_models'
 kwargs={}
 for prefix,name in (('text_detection',det_name),('text_recognition',rec_name)):
  kwargs[prefix+'_model_name']=name
  if (cache/name/'inference.pdiparams').is_file():kwargs[prefix+'_model_dir']=str(cache/name)
  elif not opts.get('download_models'):raise RuntimeError('Local OCR weights missing: '+name)
 engine=PaddleOCR(lang=LANGS[language],use_doc_orientation_classify=False,use_doc_unwarping=False,use_textline_orientation=False,device='cpu',**kwargs)
 predictions=list(engine.predict(np.array(im.convert('RGB'))));records=[]
 for pred in predictions:
  data=pred.json
  if isinstance(data,str):data=json.loads(data)
  records+=normalize_result(data.get('res',data),language)
 if opts.get('download_models'):
  files={str(p):digest(p) for p in (ROOT/'models/paddlex').rglob('*') if p.is_file()}
  if not files:raise RuntimeError('No downloaded OCR model artifacts found to lock')
  atomic_json(receipt,{'language':language,'paddleocr':version('paddleocr'),'files':files,'trust':'first-download integrity receipt'})
 mask=Image.new('L',im.size);draw=ImageDraw.Draw(mask)
 for record in records:draw.polygon([tuple(p) for p in record['polygon']],fill=255)
 mask.save(out/'text-mask.png');atomic_json(out/'text.json',records)
 return {'backend':'cpu','language':language,'records':records,'mask':str(out/'text-mask.png'),'quality':{'recognized_regions':len(records),'mean_confidence':sum(x['confidence'] for x in records)/len(records) if records else None,'empty_result':not records}}
