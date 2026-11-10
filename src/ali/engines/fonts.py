from pathlib import Path
import json
import numpy as np
from PIL import Image,ImageDraw,ImageFont,ImageOps
from ali.runtime import atomic_json,digest
from .common import load_image

def ink(image):
 arr=np.array(image.convert('L'));border=np.concatenate((arr[0,:],arr[-1,:],arr[:,0],arr[:,-1]));bg=float(np.median(border));return np.abs(arr.astype(float)-bg)>45

def shape(mask,size=(192,64)):
 y,x=np.where(mask)
 if not len(x):raise ValueError('No visible glyphs')
 crop=Image.fromarray(mask[min(y):max(y)+1,min(x):max(x)+1].astype(np.uint8)*255)
 return np.array(crop.resize(size,Image.Resampling.BILINEAR))>127

def render(text,font,size,spacing):
 f=ImageFont.truetype(font['path'],size,index=font.get('face_index',0));width=int(sum(f.getlength(c) for c in text)+max(0,len(text)-1)*spacing+size*2)
 im=Image.new('L',(max(1,width),size*3),255);draw=ImageDraw.Draw(im);x=size
 for i,c in enumerate(text):draw.text((size+f.getlength(text[:i])+i*spacing,size*2),c,font=f,fill=0,anchor='ls')
 return im

def infer(req):
 opts=req['options'];out=Path(req['out']);text=opts.get('text','')
 if not text:raise ValueError('Font matching needs human-confirmed text (not invented OCR)')
 catalog=json.loads(Path(opts['index']).read_text());target=shape(ink(load_image(req['source'])));results=[]
 for f in catalog['fonts']:
  if digest(f['path'])!=f['sha256'] or digest(f['license_file'])!=f['license_sha256']:raise ValueError('Font/license integrity mismatch')
  if any(ord(c) not in f['codepoints'] for c in text if not c.isspace()):continue
  best=None
  for spacing in (-1,0,1,2):
   preview=render(text,f,48,spacing);candidate=shape(ink(preview));intersection=(target&candidate).sum();union=(target|candidate).sum();score=float(intersection/union) if union else 0
   if best is None or score>best['similarity']:best={'family':f['family'],'path':f['path'],'weight':f['weight'],'spacing_at_48px':spacing,'similarity':score,'score_metric':'normalized_binary_IoU','license':f['license'],'identity_verified':False,'font_size_estimate':'normalized comparison, original point size unknown','preview_image':preview}
  results.append(best)
 results.sort(key=lambda x:x['similarity'],reverse=True);top=results[:5]
 for n,x in enumerate(top):
  fp=out/f'candidate_{n+1}.png';x.pop('preview_image').save(fp);x['preview']=str(fp)
 selected=opts.get('selected_font')
 if selected and selected not in [x['path'] for x in top]:raise ValueError('Manual selection must identify a reported candidate path')
 atomic_json(out/'font-candidates.json',{'text':text,'top5':top,'manual_selection':selected,'needs_human_confirmation':selected is None})
 return {'backend':'cpu','top5':top,'manual_selection':selected,'quality':{'candidates_scored':len(results),'script_coverage_checked':True,'limitations':['normalized IoU is not font identity','rotation/perspective and complex shaping are not modeled','variable font weight axes not searched; static face weight reported']}}
