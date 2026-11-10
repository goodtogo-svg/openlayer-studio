"""Uniform full-asset registration and independent transparent-aperture discovery."""
import math
import numpy as np
from PIL import Image,ImageDraw


def enclosed_opening(alpha, seed, threshold=128):
    raw=np.where(np.array(alpha.convert('L'))>=threshold,255,0).astype('uint8')
    x,y=seed
    if not 0<=x<raw.shape[1] or not 0<=y<raw.shape[0] or raw[y,x]!=0:raise ValueError('Opening seed must be transparent')
    # Copy the image itself: shared numpy storage is not a mutable flood-fill target.
    filled=Image.fromarray(raw).copy();ImageDraw.floodfill(filled,(x,y),128)
    hole=np.array(filled)==128
    if not hole.any() or hole[0].any() or hole[-1].any() or hole[:,0].any() or hole[:,-1].any():raise ValueError('Aperture connects to exterior; cannot identify a closed opening')
    return Image.fromarray(hole.astype('uint8')*255)


def anchored_transform(source_top,source_ground,source_door_x,target_top,target_ground,target_door_x):
    values=(source_top,source_ground,source_door_x,target_top,target_ground,target_door_x)
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in values):raise ValueError('Finite registration anchors required')
    if source_ground<=source_top or target_ground<=target_top:raise ValueError('Ground must be below top')
    scale=(target_ground-target_top)/(source_ground-source_top)
    return {'scale':scale,'tx':target_door_x-source_door_x*scale,'ty':target_top-source_top*scale}


def map_point(p,transform):
    return [p[0]*transform['scale']+transform['tx'],p[1]*transform['scale']+transform['ty']]


def place_uniform(image,canvas,transform):
    scale=transform['scale'];tx=transform['tx'];ty=transform['ty']
    if not math.isfinite(scale) or scale<=0:raise ValueError('Invalid registration scale')
    coeff=(1/scale,0,-tx/scale,0,1/scale,-ty/scale)
    if image.mode=='RGBA':return image.convert('RGBa').transform(canvas,Image.Transform.AFFINE,coeff,Image.Resampling.BICUBIC).convert('RGBA')
    return image.transform(canvas,Image.Transform.AFFINE,coeff,Image.Resampling.BICUBIC)
