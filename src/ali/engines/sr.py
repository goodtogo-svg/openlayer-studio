"""True Real-ESRGAN RRDB inference; padded overlapping tiles with weighted blending."""
from pathlib import Path
import numpy as np
from PIL import Image
from .common import load_image,torch_backend,checkpoint

def blend_tiles(rgb,predict,tile=128,overlap=16,scale=4):
 h,w=rgb.shape[:2]
 if tile<16 or overlap<0 or overlap>=tile//2:raise ValueError('Invalid tile/overlap')
 if h*w*scale*scale>64_000_000:raise ValueError('SR exceeds 64 MP output guard')
 accum=np.zeros((h*scale,w*scale,3),np.float32);weights=np.zeros((h*scale,w*scale,1),np.float32)
 for y in range(0,h,tile):
  for x in range(0,w,tile):
   y0=max(0,y-overlap);x0=max(0,x-overlap);y1=min(h,y+tile+overlap);x1=min(w,x+tile+overlap)
   pred=predict(rgb[y0:y1,x0:x1]);ph,pw=pred.shape[:2]
   if (ph,pw)!=((y1-y0)*scale,(x1-x0)*scale):raise RuntimeError('Model did not produce native 4x output')
   wy=np.ones(ph,np.float32);wx=np.ones(pw,np.float32);fade=overlap*scale
   if fade:
    if y0>0:wy[:fade]=np.linspace(.01,1,fade)
    if y1<h:wy[-fade:]=np.linspace(1,.01,fade)
    if x0>0:wx[:fade]=np.linspace(.01,1,fade)
    if x1<w:wx[-fade:]=np.linspace(1,.01,fade)
   weight=(wy[:,None]*wx[None,:])[:,:,None];sl=np.s_[y0*scale:y1*scale,x0*scale:x1*scale]
   accum[sl]+=pred*weight;weights[sl]+=weight
 return np.rint(accum/np.maximum(weights,1e-8)).clip(0,255).astype(np.uint8)

def infer(req):
 import torch,sys
 import torchvision.transforms.functional as functional
 # BasicSR 1.4.2 imports an API removed by torchvision 0.17; explicit compatibility alias.
 sys.modules.setdefault('torchvision.transforms.functional_tensor',functional)
 from basicsr.archs.rrdbnet_arch import RRDBNet
 opts=req['options'];strategy=opts.get('strategy','photo')
 if strategy not in ('photo','illustration','transparent'):raise ValueError('Invalid SR strategy')
 name='sr-illustration' if strategy=='illustration' else 'sr-photo';path,spec=checkpoint(name);backend=torch_backend(opts.get('backend','auto'));torch.set_num_threads(4)
 model=RRDBNet(num_in_ch=3,num_out_ch=3,num_feat=64,num_block=spec['blocks'],num_grow_ch=32,scale=4)
 weights=torch.load(path,map_location='cpu',weights_only=True);model.load_state_dict(weights.get('params_ema',weights.get('params',weights)),strict=True);model.eval().to(backend)
 im=load_image(req['source']);rgba=np.array(im);rgb=rgba[:,:,:3]
 # Transparent pixels have undefined RGB; nearest valid RGB fill avoids black halos.
 if strategy=='transparent' and np.any(rgba[:,:,3]<255):
  from scipy.ndimage import distance_transform_edt
  invalid=rgba[:,:,3]==0
  if np.any(~invalid):
   indices=distance_transform_edt(invalid,return_distances=False,return_indices=True);rgb=rgb[indices[0],indices[1]]
 def predict(crop):
  tensor=torch.from_numpy(crop.copy()).permute(2,0,1).unsqueeze(0).float().to(next(model.parameters()).device)/255
  with torch.inference_mode():result=model(tensor).squeeze(0).clamp(0,1).permute(1,2,0).cpu().numpy()*255
  return result
 fallback=None
 try:result=blend_tiles(rgb,predict,int(opts.get('tile',128)),int(opts.get('overlap',16)))
 except RuntimeError as exc:
  if backend!='mps':raise
  fallback=str(exc);model.to('cpu');backend='cpu';result=blend_tiles(rgb,predict,int(opts.get('tile',128)),int(opts.get('overlap',16)))
 # Alpha is a coverage channel, not neural detail; preserve it via explicit interpolation.
 alpha=im.getchannel('A').resize((im.width*4,im.height*4),Image.Resampling.BILINEAR)
 output=Image.fromarray(result);output.putalpha(alpha);fp=Path(req['out'])/'sr4x.png';output.save(fp)
 return {'backend':backend,'cpu_fallback_reason':fallback,'model':name,'strategy':strategy,'image':str(fp),'scale':4,'native_ai_rgb':True,'alpha_method':'bilinear coverage, not AI','quality':{'output_size':list(output.size),'tile':int(opts.get('tile',128)),'overlap_blending':True,'detail_accuracy':'unverified; generative restoration can invent detail'}}
