"""Explicit reviewed piecewise canvas geometry, shared by assets and light anchors."""
import numpy as np
from PIL import Image


def validate_axis(source, target, length):
    if len(source)!=len(target) or len(source)<2 or source[0]!=0 or target[0]!=0 or source[-1]!=length or target[-1]!=length:
        raise ValueError('Warp axis must span canvas and pair all knots')
    for axis in (source,target):
        if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not np.isfinite(x) for x in axis) or any(b<=a for a,b in zip(axis,axis[1:])):
            raise ValueError('Warp must be finite and monotonic; folds forbidden')


def point(p, geometry):
    return [float(np.interp(p[0],geometry['x_source'],geometry['x_target'])),float(np.interp(p[1],geometry['y_source'],geometry['y_target']))]


def warp(image, geometry):
    w,h=image.size
    validate_axis(geometry['x_source'],geometry['x_target'],w);validate_axis(geometry['y_source'],geometry['y_target'],h)
    sx,tx,sy,ty=[geometry[k] for k in ('x_source','x_target','y_source','y_target')]
    mesh=[]
    for j in range(len(sy)-1):
        for i in range(len(sx)-1):
            box=tuple(round(v) for v in (tx[i],ty[j],tx[i+1],ty[j+1]))
            if box[2]<=box[0] or box[3]<=box[1]:raise ValueError('Warp cell collapsed after rounding')
            quad=(sx[i],sy[j],sx[i],sy[j+1],sx[i+1],sy[j+1],sx[i+1],sy[j])
            mesh.append((box,quad))
    # Premultiplied interpolation avoids dark fringes at transparent boundaries.
    if image.mode=='RGBA':return image.convert('RGBa').transform((w,h),Image.Transform.MESH,mesh,Image.Resampling.BICUBIC).convert('RGBA')
    return image.transform((w,h),Image.Transform.MESH,mesh,Image.Resampling.BICUBIC)
