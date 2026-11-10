"""Image-derived style estimates, never claims recovery of original PSD metadata."""
from pathlib import Path
import json
import numpy as np
from PIL import Image,ImageFilter
from .runtime import atomic_json,digest


def estimate_style(image, box):
    image=image.convert('RGB');x1,y1,x2,y2=map(int,box)
    if not (0<=x1<x2<=image.width and 0<=y1<y2<=image.height):
        raise ValueError('Style region outside image')
    a=np.asarray(image.crop((x1,y1,x2,y2))).astype(float)
    border=np.concatenate([a[0],a[-1],a[:,0],a[:,-1]])
    background=np.median(border,axis=0)
    warm=(a[:,:,0]>205)&(a[:,:,1]>135)&(a[:,:,0]>a[:,:,2]*1.05)
    white=(a.min(axis=2)>205)
    core=warm|white
    if core.sum()<8:
        raise ValueError('No reliable text core in style region')
    gold=bool(warm.sum()>core.sum()*.25)
    chosen=warm if gold else white
    fill=np.quantile(a[chosen],.7,axis=0).round().astype(int).tolist()
    bands=[]
    for indices in np.array_split(np.arange(a.shape[0]),3):
        samples=a[indices][chosen[indices]]
        bands.append(np.quantile(samples,.65,axis=0).round().astype(int).tolist() if len(samples) else fill)
    spread=float(np.max(np.ptp(np.array(bands),axis=0)))
    core_im=Image.fromarray(core.astype('uint8')*255)
    inner=np.array(core_im.filter(ImageFilter.MaxFilter(5)))>0
    energies=[]
    for radius in [4,6,8,12,16,20,24]:
        outer=np.array(core_im.filter(ImageFilter.MaxFilter(radius*2+1)))>0
        ring=outer&~inner
        energy=float(np.maximum(a[:,:,0]-background[0],0)[ring].mean()/max(1,fill[0]-background[0])) if ring.any() else 0
        energies.append([radius,energy])
    enabled=bool(gold and energies[1][1]>.035)
    size=max([radius for radius,e in energies if e>.055] or [4])
    ring=(np.array(core_im.filter(ImageFilter.MaxFilter(17)))>0)&~inner
    residual=np.maximum(a-background,0)[ring]
    positive=residual[residual[:,0]>12]
    tone=np.median(positive,axis=0) if len(positive) else np.array([255,90,50])
    glow_color=np.clip(tone/max(tone.max(),1)*255,0,255).round().astype(int).tolist()
    return {'provenance':'estimated_from_flat_RGB_not_original_layer_metadata',
            'original_parameters_recovered':False,'needs_visual_review':True,
            'fill_rgb':fill,
            'gradient':{'enabled':bool(gold and spread>12),'angle':90,
                        'stops':[{'position':i*50,'rgb':rgb} for i,rgb in enumerate(bands[::-1])]},
            'outer_glow':{'enabled':enabled,'blend_mode':'screen','rgb':glow_color,
                          'opacity':min(85,max(35,round(energies[0][1]*140))),
                          'size_px':size,'spread_percent':0},
            'unresolved_effects':['bevel/emboss versus painted highlights cannot be uniquely inferred'],
            'evidence':{'core_pixels':int(core.sum()),'background_rgb':background.round().astype(int).tolist(),
                        'red_halo_energy_by_radius':energies}}


def estimate_report(source,regions,out):
    source=Path(source).resolve();out=Path(out).resolve()
    if out.exists():raise FileExistsError('Refusing to overwrite style report')
    plan=json.loads(Path(regions).read_text())
    with Image.open(source) as image:
        layers=[{'id':r['id'],'text':r.get('text'),'font_substitute':r.get('font'),
                 'tracking':r.get('tracking'), 'box':r['box'],**estimate_style(image,r['box'])} for r in plan]
    result={'schema':'ali-text-style/1','source':str(source),'source_sha256':digest(source),'layers':layers}
    atomic_json(out,result);return result
