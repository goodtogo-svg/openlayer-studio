"""Source-traced reconstruction; silhouettes/pose come from reviewed original masks.

Materials and de-lighting remain estimates, never recovered original assets.
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def bezier(control, count=96):
    p=np.asarray(control,float);t=np.linspace(0,1,count)[:,None]
    return (1-t)**3*p[0]+3*(1-t)**2*t*p[1]+3*(1-t)*t*t*p[2]+t**3*p[3]


def polygon_mask(size,points):
    im=Image.new('L',(size[0]*2,size[1]*2),0)
    ImageDraw.Draw(im).polygon([tuple(float(v)*2 for v in p) for p in points],fill=255)
    return im.resize(size,Image.Resampling.LANCZOS)


def doorway_masks(size,outer,inner,bottom):
    outside=[[outer[0][0],bottom],*bezier(outer).tolist(),[outer[-1][0],bottom]]
    inside=[[inner[0][0],bottom],*bezier(inner).tolist(),[inner[-1][0],bottom]]
    a=polygon_mask(size,outside);b=polygon_mask(size,inside)
    ring=Image.fromarray(np.uint8(np.maximum(np.array(a).astype(int)-np.array(b).astype(int),0)))
    return b,ring


def texture_plane(texture,size,polygon,rgb):
    """Project a neutral texture quad to a reviewed source quadrilateral."""
    if len(polygon)!=4:raise ValueError('Four ordered plane vertices required')
    w,h=texture.size
    # PIL perspective expects destination -> source, solved without CV dependencies.
    target=[(0,0),(w,0),(w,h),(0,h)];matrix=[];values=[]
    for (x,y),(u,v) in zip(polygon,target):
        matrix.extend([[x,y,1,0,0,0,-u*x,-u*y],[0,0,0,x,y,1,-v*x,-v*y]]);values.extend([u,v])
    try:coeff=np.linalg.solve(np.asarray(matrix,float),np.asarray(values,float))
    except np.linalg.LinAlgError as e:raise ValueError('Degenerate material plane') from e
    neutral=np.array(texture.convert('RGB'),dtype=float)
    # Preserve fine texture, remove the donor's low-frequency color/exposure.
    low=np.array(texture.convert('RGB').filter(ImageFilter.GaussianBlur(4)),dtype=float)
    neutral=np.uint8(np.clip(np.asarray(rgb)+(neutral-low)*.32,0,255))
    projected=Image.fromarray(neutral).transform(size,Image.Transform.PERSPECTIVE,coeff,Image.Resampling.BICUBIC)
    projected.putalpha(polygon_mask(size,polygon));return projected


def neutralize_person(source,alpha):
    """Conservative tone/WB estimate with exact source pose and physical alpha."""
    x=np.array(source.convert('RGB'),dtype=float)/255
    # Compress baked highlights without inventing anatomy/garment detail.
    peak=x.max(axis=2,keepdims=True);x*=np.where(peak>.86,(.86+(peak-.86)*.6)/np.maximum(peak,1e-6),1)
    # Remove warm spill from bright scarf only (position/material reviewed for this case).
    yy=np.arange(x.shape[0])[:,None];xx=np.arange(x.shape[1])[None,:]
    scarf=(yy>700)&(yy<760)&(xx>980)&(xx<1110)&(x.min(axis=2)>.48)
    luminance=x.mean(axis=2);neutral=np.stack([luminance*1.02,luminance,luminance*.96],axis=2)
    x[scarf]=x[scarf]*.2+neutral[scarf]*.8
    result=Image.fromarray(np.uint8(np.clip(x*255,0,255)));result.putalpha(alpha.convert('L'));return result
