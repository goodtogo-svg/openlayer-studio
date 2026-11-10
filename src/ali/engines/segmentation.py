"""SAM2.1 segmentation with explicitly inferred alpha/background/order."""
import json
from pathlib import Path
import numpy as np
from PIL import Image
from .common import load_image,torch_backend,checkpoint

def validate_prompts(data,width,height):
 if data.get('schema')!='ali-prompts/1':raise ValueError('Expected ali-prompts/1')
 objects=data.get('objects',[])
 for item in objects:
  points=item.get('points',[]);labels=item.get('labels',[])
  if len(points)!=len(labels) or any(x not in (0,1) for x in labels):raise ValueError('Point/label mismatch')
  for x,y in points:
   if not (0<=x<width and 0<=y<height):raise ValueError('Point outside image')
  if item.get('box'):
   x1,y1,x2,y2=item['box']
   if not (0<=x1<x2<=width and 0<=y1<y2<=height):raise ValueError('Invalid box')
  if not points and not item.get('box'):raise ValueError('Object needs points or box')
 return objects

def _infer(req):
 import torch,cv2
 from sam2.build_sam import build_sam2
 from sam2.sam2_image_predictor import SAM2ImagePredictor
 from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
 opts=req['options'];out=Path(req['out']);im=load_image(req['source']);rgb=np.array(im.convert('RGB'));path,spec=checkpoint(opts.get('model','sam2-tiny'));backend=torch_backend(opts.get('backend','auto'))
 torch.set_num_threads(4)
 model=build_sam2(spec['config'],path,device=backend,apply_postprocessing=False)
 records=[]
 with torch.inference_mode():
  if opts.get('prompts'):
   items=validate_prompts(json.loads(Path(opts['prompts']).read_text()),im.width,im.height);predictor=SAM2ImagePredictor(model);predictor.set_image(rgb)
   for item in items:
    masks,scores,_=predictor.predict(point_coords=np.array(item['points']) if item.get('points') else None,point_labels=np.array(item['labels']) if item.get('points') else None,box=np.array(item['box']) if item.get('box') else None,multimask_output=True)
    best=int(np.argmax(scores));records.append({'mask':masks[best],'score':float(scores[best]),'id':item.get('id',str(len(records))) ,'corrections':item.get('corrections',[])})
  else:
   generator=SAM2AutomaticMaskGenerator(model,points_per_side=8,points_per_batch=8,pred_iou_thresh=0.7,stability_score_thresh=0.8)
   for x in sorted(generator.generate(rgb),key=lambda x:x['area'],reverse=True)[:24]:records.append({'mask':x['segmentation'],'score':float(x['predicted_iou']),'id':str(len(records)),'corrections':[]})
 if not records:raise RuntimeError('SAM returned no masks')
 layers=[];union=np.zeros((im.height,im.width),np.uint8)
 for n,item in enumerate(records):
  mask=np.array(item['mask'],dtype=np.uint8)*255
  for correction in item['corrections']:
   polygon=np.array(correction['polygon'],np.int32)
   if len(polygon)<3 or correction['operation'] not in ('add','subtract'):raise ValueError('Invalid correction polygon')
   cv2.fillPoly(mask,[polygon],255 if correction['operation']=='add' else 0)
  # Closed-form alpha estimated from a SAM-derived trimap, not original alpha.
  from pymatting import estimate_alpha_cf
  kernel=np.ones((5,5),np.uint8)
  interior=cv2.erode(mask,kernel)>0;exterior=cv2.dilate(mask,kernel)==0
  if not interior.any() or not exterior.any():raise RuntimeError('Mask cannot form a valid matting trimap')
  if im.width*im.height>1_000_000:raise ValueError('Closed-form matting limited to 1 MP; crop before inference')
  trimap=np.full(mask.shape,0.5);trimap[interior]=1;trimap[exterior]=0
  alpha=(estimate_alpha_cf(rgb.astype(np.float64)/255,trimap)*255).clip(0,255).astype(np.uint8)
  layer=np.array(im);layer[:,:,3]=np.minimum(layer[:,:,3],alpha)
  fp=out/f'object_{n:03d}.png';Image.fromarray(layer).save(fp);mp=out/f'mask_{n:03d}.png';Image.fromarray(mask).save(mp);union=np.maximum(union,mask)
  layers.append({'id':item['id'],'image':str(fp),'mask':str(mp),'confidence':item['score'],'z_index':n+1,'order_provenance':'area heuristic; human review required','alpha_method':'closed_form_pymatting_sam_trimap_inferred','provenance':'sam2_prediction'})
 bg=cv2.inpaint(cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR),union,3,cv2.INPAINT_TELEA);bgp=out/'background_inferred.png';Image.fromarray(cv2.cvtColor(bg,cv2.COLOR_BGR2RGB)).save(bgp)
 return {'backend':backend,'model':opts.get('model','sam2-tiny'),'layers':layers,'background':{'image':str(bgp),'provenance':'inferred_opencv_telea_not_original','is_original':False},'quality':{'object_count':len(layers),'fine_alpha_matting':'closed_form_from_predicted_trimap; hair/translucency quality unverified','background_model_inpainting':'not_implemented','layer_order':'unverified_heuristic'}}


def infer(req):
 try:return _infer(req)
 except (RuntimeError,NotImplementedError) as exc:
  from .common import torch_backend
  if req['options'].get('backend','auto')!='auto' or torch_backend('auto')!='mps':raise
  import copy,gc,torch
  reason=str(exc);gc.collect();torch.mps.empty_cache()
  retry=copy.deepcopy(req);retry['options']['backend']='cpu'
  result=_infer(retry);result['cpu_fallback_reason']=reason
  return result
