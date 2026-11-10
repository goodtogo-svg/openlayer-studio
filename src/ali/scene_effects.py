"""Deterministic effect plates, separately parameterized from clean component pixels.

These are calibrated appearance reconstructions, not recovered original filters.
Blur/shape parameters regenerate the plate; blend and opacity remain native PSD values.
"""
import hashlib
import json
import math
from pathlib import Path

from .adobe_masks import PHOTOSHOP_MASK_JS
from .grading import validate_curves, PHOTOSHOP_GRADING_JS
from .appearance import validate_effects, PHOTOSHOP_APPEARANCE_JS
from .native_styles import validate_bevel, PHOTOSHOP_BEVEL_JS
import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def number(value, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError('Effect parameter outside supported range')
    return value


def validate(plan):
    w,h=plan['size']
    if not all(isinstance(x,int) and not isinstance(x,bool) and 1 <= x <= 8192 for x in (w,h)) or w*h>20_000_000:
        raise ValueError('Invalid canvas')
    for key,checker in (('component_grading',validate_curves),('component_fx',validate_effects)):
        config=plan.get(key,{})
        if not isinstance(config,dict) or set(config)-{'portal','key','person','gifts'}:raise ValueError('Unknown component filter target')
        for value in config.values():checker(value)
    ids=[]
    for layer in plan['layers']:
        name=layer['id'];ids.append(name)
        if 'native_bevel' in layer:validate_bevel(layer['native_bevel'])
        if not name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in name):raise ValueError('Unsafe effect id')
        if layer['owner'] not in ('portal','key','person','gifts','environment'):raise ValueError('Unknown component owner')
        if layer['blend'] not in ('screen','normal','linear_dodge','multiply','color'):raise ValueError('Unsupported blend')
        number(layer['opacity'],0,100);number(layer['blur_px'],0,100);number(layer.get('intensity',1),0,16)
        if len(layer['rgb'])!=3:raise ValueError('RGB required')
        for v in layer['rgb']:number(v,0,255)
        if layer.get('placement','front') not in ('front','behind'):raise ValueError('Invalid effect placement')
        if 'arch_rise_px' in layer:number(layer['arch_rise_px'],.1,max(w,h))
        if 'clip_stage' in layer and layer['clip_stage'] not in ('pre_blur','post_blur'):raise ValueError('Invalid clip stage')
        if 'angle_deg' in layer:number(layer['angle_deg'],-360,360)
        if 'light_position' in layer:
            if len(layer['light_position'])!=2:raise ValueError('Light needs XY')
            for v in layer['light_position']:number(v,-30000,30000)
            number(layer.get('ambient_floor',.1),0,1)
        kind=layer['kind']
        if kind not in ('arch','gradient','ellipse','curves','stars','particles','rim'):raise ValueError('Unsupported primitive')
        if kind in ('arch','gradient','ellipse','particles'):
            box=layer['box']
            if len(box)!=4:raise ValueError('Four box coordinates required')
            for v in box:number(v,-max(w,h)*2,max(w,h)*3)
            if box[2]<=box[0] or box[3]<=box[1]:raise ValueError('Empty bounds')
        if kind in ('arch','gradient'):
            for color in layer['gradient_rgb']:
                if len(color)!=3:raise ValueError('Gradient RGB required')
                for v in color:number(v,0,255)
            if len(layer['gradient_rgb'])!=2:raise ValueError('Two gradient endpoints required')
        if kind=='curves':
            number(layer['width_px'],.1,100)
            for segment in layer['segments']:
                if len(segment)!=4 or any(len(p)!=2 for p in segment):raise ValueError('Cubic Bezier requires four XY pairs')
                for p in segment:
                    for v in p:number(v,-max(w,h)*2,max(w,h)*3)
        if kind=='stars':
            for star in layer['stars']:
                if len(star)!=4:raise ValueError('Star needs x,y,radius,rotation')
                for v in star:number(v,-30000,30000)
                number(star[2],.1,250)
        if kind=='particles':
            if not isinstance(layer['seed'],int) or isinstance(layer['seed'],bool) or not 0<=layer['seed']<2**32:raise ValueError('Integer seed required')
            if not isinstance(layer['count'],int) or isinstance(layer['count'],bool) or not 0<=layer['count']<=2000:raise ValueError('Particle count outside limit')
            number(layer['radius_px'],.2,20)
        if kind=='rim':number(layer['width_px'],1,30)
    if len(set(ids))!=len(ids):raise ValueError('Duplicate effect id')


def render_layer(layer,size,root):
    w,h=size;scale=2
    mask=Image.new('L',(w*scale,h*scale),0);d=ImageDraw.Draw(mask)
    xy=lambda p:tuple(float(x)*scale for x in p)
    kind=layer['kind']
    if kind=='gradient':
        d.rectangle(xy(layer['box']),fill=255)
    elif kind=='arch':
        x0,y0,x1,y1=layer['box'];radius=layer.get('arch_rise_px',(x1-x0)/2)
        d.ellipse(xy((x0,y0,x1,y0+radius*2)),fill=255)
        d.rectangle(xy((x0,y0+radius,x1,y1)),fill=255)
        # Explicit bottom clipping prevents ellipse extending below short doorway.
        clip=Image.new('L',mask.size,0);ImageDraw.Draw(clip).rectangle(xy((x0,y0,x1,y1)),fill=255)
        mask=Image.fromarray(np.minimum(np.array(mask),np.array(clip)))
    elif kind=='ellipse':
        # Soft radial distribution, not a flat blurred oval.
        x0,y0,x1,y1=layer['box'];yy,xx=np.ogrid[:h,:w]
        theta=math.radians(layer.get('angle_deg',0));dx=xx-(x0+x1)/2;dy=yy-(y0+y1)/2
        local_x=dx*math.cos(theta)+dy*math.sin(theta);local_y=-dx*math.sin(theta)+dy*math.cos(theta)
        field=np.maximum(0,1-(local_x/((x1-x0)/2))**2-(local_y/((y1-y0)/2))**2)**2
        mask=Image.fromarray(np.uint8(field*255)).resize((w*scale,h*scale),Image.Resampling.BILINEAR)
    elif kind=='curves':
        for control in layer['segments']:
            points=np.asarray(control,float);t=np.linspace(0,1,160)[:,None]
            curve=(1-t)**3*points[0]+3*(1-t)**2*t*points[1]+3*(1-t)*t*t*points[2]+t**3*points[3]
            d.line([xy(p) for p in curve],fill=255,width=max(1,round(layer['width_px']*scale)),joint='curve')
    elif kind=='stars':
        for x,y,r,angle in layer['stars']:
            pts=[]
            for i in range(8):
                theta=math.radians(angle)+i*math.pi/4;radius=r if i%2==0 else r*.13
                pts.append(xy((x+math.cos(theta)*radius,y+math.sin(theta)*radius)))
            d.polygon(pts,fill=255)
    elif kind=='particles':
        rng=np.random.default_rng(layer['seed']);x0,y0,x1,y1=layer['box']
        for _ in range(layer['count']):
            x=rng.uniform(x0,x1);y=rng.uniform(y0,y1);r=rng.uniform(.35,1)*layer['radius_px']
            d.ellipse(xy((x-r,y-r,x+r,y+r)),fill=int(rng.uniform(75,255)))
    elif kind=='rim':
        path=Path(layer['alpha_mask']);path=path if path.is_absolute() else root/path
        with Image.open(path) as im:
            if im.size!=(w,h):raise ValueError('Rim mask dimensions differ from canvas')
            a=im.convert('L')
        radius=round(layer['width_px']);inside=a.filter(ImageFilter.MinFilter(radius*2+1))
        edge=np.maximum(np.array(a).astype(float)-np.array(inside).astype(float),0)
        if 'light_position' in layer:
            dy,dx=np.gradient(np.array(a.filter(ImageFilter.GaussianBlur(1.5)),dtype=float));yy,xx=np.ogrid[:h,:w]
            lx=layer['light_position'][0]-xx;ly=layer['light_position'][1]-yy
            dot=-(dx*lx+dy*ly)/(np.hypot(dx,dy)*np.hypot(lx,ly)+1e-6)
            ambient=layer.get('ambient_floor',.1);edge*=ambient+(1-ambient)*np.clip(dot,0,1)
        mask=Image.fromarray(np.uint8(edge)).resize(mask.size,Image.Resampling.BILINEAR)
    mask=mask.resize((w,h),Image.Resampling.LANCZOS)
    def constrain(current):
        values=np.array(current,dtype=float)/255
        for key,invert in (('clip_mask',False),('exclude_mask',True)):
            if key not in layer:continue
            p=Path(layer[key]);p=p if p.is_absolute() else root/p
            with Image.open(p) as im:
                if im.size!=(w,h):raise ValueError('Effect constraint mask dimensions differ')
                coverage=np.array(im.convert('L'),dtype=float)/255
            values*=1-coverage if invert else coverage
        return Image.fromarray(np.uint8(np.clip(values*255,0,255)))
    if layer.get('clip_stage','post_blur')=='pre_blur':mask=constrain(mask)
    if layer['blur_px']:mask=mask.filter(ImageFilter.GaussianBlur(layer['blur_px']))
    if layer.get('intensity',1)!=1:mask=Image.fromarray(np.uint8(np.clip(np.array(mask,dtype=float)*layer['intensity'],0,255)))
    if layer.get('clip_stage','post_blur')=='post_blur':mask=constrain(mask)
    rgb=np.zeros((h,w,3),dtype='uint8');rgb[:]=layer['rgb']
    if kind in ('arch','gradient'):
        y0,y1=layer['box'][1],layer['box'][3]
        t=np.clip((np.arange(h)-y0)/(y1-y0),0,1)[:,None]
        top,bottom=np.asarray(layer['gradient_rgb'],float)
        rgb[:]=np.uint8(top*(1-t)+bottom*t)[:,None,:]
    return Image.fromarray(np.dstack([rgb,np.array(mask)]))


def compile_effects(plan_path,output):
    plan_path=Path(plan_path).resolve();root=plan_path.parent;plan=json.loads(plan_path.read_text());validate(plan)
    out=Path(output).resolve()
    if out.exists():raise ValueError('Output exists; choose new job')
    base=Path(plan['base_psd']);base=base if base.is_absolute() else root/base
    if not base.is_file():raise ValueError('Missing reviewed PSD')
    # Validate inputs and render before publishing any job artifacts.
    rendered=[];native_masks=[]
    for layer in plan['layers']:
        use_native=plan.get('native_constraints') is True and layer.get('native_constraint',True) is True and layer.get('clip_stage','post_blur')=='post_blur' and any(k in layer for k in ('clip_mask','exclude_mask'))
        mask=None
        if use_native:
            values=np.ones((plan['size'][1],plan['size'][0]),dtype=float)
            for key,invert in (('clip_mask',False),('exclude_mask',True)):
                if key not in layer:continue
                path=Path(layer[key]);path=path if path.is_absolute() else root/path
                with Image.open(path) as im:
                    if list(im.size)!=plan['size']:raise ValueError('Native constraint mask dimensions differ')
                    coverage=np.array(im.convert('L'),dtype=float)/255
                values*=1-coverage if invert else coverage
            mask=Image.fromarray(np.uint8(values*255))
            raw={k:v for k,v in layer.items() if k not in ('clip_mask','exclude_mask')}
            rendered.append(render_layer(raw,plan['size'],root))
        else:rendered.append(render_layer(layer,plan['size'],root))
        native_masks.append(mask)
    out.mkdir(parents=True);(out/'assets').mkdir()
    receipt={'schema':'ali-parametric-effects/1','status':'script_generated_not_executed','base_psd_sha256':hashlib.sha256(base.read_bytes()).hexdigest(),
             'parameters_sha256':hashlib.sha256(plan_path.read_bytes()).hexdigest(),'is_original':False,
             'parameter_origin':'source-guided manual calibration; not recovered original filters','component_grading':plan.get('component_grading',{}),'component_fx':plan.get('component_fx',{}),'layers':[]}
    q=lambda s:json.dumps(str(s),ensure_ascii=True)
    lines=['#target photoshop','var oldDialogs=app.displayDialogs,oldUnits=app.preferences.rulerUnits,ownedCopy=null;try{','app.displayDialogs=DialogModes.NO;app.preferences.rulerUnits=Units.PIXELS;',
           'var base=app.open(new File('+q(base)+'));var doc=base.duplicate("Parametric appearance reconstruction");ownedCopy=doc;',
           'var root=doc.layerSets.getByName("04 Component masters - observed plus inferred");',
           'function place(p,n,parent){var d=new ActionDescriptor();d.putPath(charIDToTypeID("null"),new File(p));executeAction(charIDToTypeID("Plc "),d,DialogModes.NO);var l=doc.activeLayer;l.name=n;l.move(parent,ElementPlacement.INSIDE);return l;}',
           'var owners={};',
           'var ids=["portal","key","person","gifts"];for(var i=0;i<ids.length;i++){var p=root.layerSets.getByName(ids[i]);for(var j=p.layerSets.length-1;j>=0;j--)if((p.layerSets[j].name==="PARAMETRIC FX - "+ids[i]||p.layerSets[j].name==="PARAMETRIC FX BACK - "+ids[i]))p.layerSets[j].remove();}']
    lines.append('for(var e=root.layerSets.length-1;e>=0;e--)if(root.layerSets[e].name==="ENVIRONMENT FX")root.layerSets[e].remove();')
    lines.append(PHOTOSHOP_MASK_JS)
    lines.append(PHOTOSHOP_BEVEL_JS)
    lines.append(PHOTOSHOP_GRADING_JS)
    lines.append(PHOTOSHOP_APPEARANCE_JS)
    for owner in sorted(set(plan.get('component_grading',{}))|set(plan.get('component_fx',{}))):
        lines += ['var parent=root.layerSets.getByName('+q(owner)+'),clean=null;for(var z=0;z<parent.artLayers.length;z++)if(parent.artLayers[z].name.indexOf("CLEAN BASE")===0)clean=parent.artLayers[z];if(!clean)throw new Error("Missing approved clean base");']
        if owner in plan.get('component_grading',{}):
            lines += ['for(var z=parent.artLayers.length-1;z>=0;z--)if(parent.artLayers[z].name==="FILTER - editable lighting curves")parent.artLayers[z].remove();',
                      'createGrade(clean,'+json.dumps(plan['component_grading'][owner])+');']
        if owner in plan.get('component_fx',{}):lines += ['applyComponentEffects(clean,'+json.dumps(plan['component_fx'][owner])+');']
    blends={'screen':'SCREEN','normal':'NORMAL','linear_dodge':'LINEARDODGE','multiply':'MULTIPLY','color':'COLORBLEND'}
    for layer,im,mask in zip(plan['layers'],rendered,native_masks):
        name=layer['id'];target=out/'assets'/f'{name}.png';im.save(target)
        owner=layer['owner'];entry={**layer,'asset':str(target),'asset_sha256':hashlib.sha256(target.read_bytes()).hexdigest()};receipt['layers'].append(entry)
        mask_path=None
        if mask is not None:
            mask_path=out/'assets'/f'{name}-NATIVE-MASK.png';mask.save(mask_path);entry['native_mask_asset']=str(mask_path)
        behind=layer.get('placement')=='behind';key=owner+('-back' if behind else '-front')
        group_name=('PARAMETRIC FX BACK - ' if behind else 'PARAMETRIC FX - ')+owner
        move='var solid=null;for(var z=0;z<parent.artLayers.length;z++)if(parent.artLayers[z].name.indexOf("CLEAN BASE")===0)solid=parent.artLayers[z];if(!solid)throw new Error("Missing clean base");owners['+q(key)+'].move(solid,ElementPlacement.PLACEAFTER);' if behind else ''
        if owner=='environment':
            create='if(!owners['+q(key)+']){owners['+q(key)+']=root.layerSets.add();owners['+q(key)+'].name="ENVIRONMENT FX";owners['+q(key)+'].move(root.layerSets.getByName("background"),ElementPlacement.PLACEBEFORE);}'
        else:create='if(!owners['+q(key)+']){var parent=root.layerSets.getByName('+q(owner)+');owners['+q(key)+']=parent.layerSets.add();owners['+q(key)+'].name='+q(group_name)+';'+move+'}'
        lines += [create,
                  'var fx=place('+q(target)+','+q(name)+',owners['+q(key)+']);fx.blendMode=BlendMode.'+blends[layer['blend']]+';fx.opacity='+str(layer['opacity'])+';',
                  ('nativeMask(fx,'+q(mask_path)+');' if mask_path else ''),
                  ('nativeBevel(fx,'+json.dumps(layer['native_bevel'])+');' if layer.get('native_bevel') else '')]
    lines += ['var target=new File('+q(out/'parametric-reconstruction.psd')+');if(target.exists)throw new Error("Refusing overwrite");var opts=new PhotoshopSaveOptions();opts.layers=true;doc.saveAs(target,opts,true,Extension.LOWERCASE);',
              '}finally{if(ownedCopy)ownedCopy.close(SaveOptions.DONOTSAVECHANGES);app.preferences.rulerUnits=oldUnits;app.displayDialogs=oldDialogs;}']
    (out/'build-photoshop.jsx').write_text('\n'.join(lines));(out/'parameters.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2));(out/'manifest.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    return receipt
