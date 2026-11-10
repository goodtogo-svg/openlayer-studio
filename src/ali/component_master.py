"""Compile reviewed component masters without confusing observed and generated pixels."""
import hashlib
import math
import json
from pathlib import Path

import numpy as np
from PIL import Image
from .occlusion import depth_order
from .adobe_masks import PHOTOSHOP_MASK_JS
from .grading import validate_curves, PHOTOSHOP_GRADING_JS
from .appearance import validate_appearance, reviewed, PHOTOSHOP_APPEARANCE_JS


def compile_master(plan_path, output):
    plan_path = Path(plan_path).resolve()
    plan = json.loads(plan_path.read_text())
    out = Path(output).resolve()
    if out.exists():
        raise ValueError('Output exists; use a new job')
    def path(value):
        p = Path(value).expanduser()
        return p.resolve() if p.is_absolute() else (plan_path.parent / p).resolve()
    source = path(plan['source'])
    with Image.open(source) as im:
        rgb = np.array(im.convert('RGB'))
    h, w = rgb.shape[:2]
    def raster(value, mode):
        with Image.open(path(value)) as im:
            if im.size != (w, h):
                raise ValueError('Asset/mask dimensions must match source canvas')
            return np.array(im.convert(mode))
    components = plan['components']
    ids = [c['id'] for c in components]
    if any(not n or any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for ch in n) for n in ids):
        raise ValueError('Unsafe component id')
    order = depth_order(ids, plan.get('behind_edges', []))
    prepared = []
    for c in components:
        if not reviewed(c):
            raise ValueError('Each component requires reviewed selection')
        support = raster(c['support_mask'], 'L') > 0
        observed = raster(c['observed_mask'], 'L') > 0
        if np.any(observed & ~support) or not support.any():
            raise ValueError('Invalid observed/support partition')
        hidden = support & ~observed
        generated = raster(c['inferred_image'], 'RGB') if hidden.any() else rgb
        if hidden.any() and not c.get('provenance'):
            raise ValueError('Inferred content requires provenance')
        appearance = c.get('appearance')
        clean = clean_support = None
        if appearance is not None:
            validate_appearance(appearance)
            validate_curves(appearance.get("grade_curves",{}))
            if appearance.get('native_source_rgb'):
                if not appearance.get('native_mask'):raise ValueError('Native source needs registered physical mask')
                with Image.open(path(appearance['native_source_rgb'])) as native:
                    if native.mode!='RGB':raise ValueError('Native high resolution source must be RGB; alpha is separate')
                transform=appearance.get('native_transform',{})
                if set(transform)!={'scale','tx','ty'} or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in transform.values()) or transform['scale']<=0:raise ValueError('Invalid native source transform')
            clean = raster(appearance['clean_image'], 'RGB')
            clean_support = raster(appearance['support_mask'], 'L')
            if not clean_support.any():raise ValueError('Empty clean physical support')
        if c.get('environment_mask'):raster(c['environment_mask'],'L')
        prepared.append((c, support, observed, hidden, generated, clean, clean_support))
    base = path(plan['base_psd'])
    if not base.is_file():
        raise ValueError('Missing base PSD with reviewed editable typography')
    out.mkdir(parents=True)
    assets = out / 'assets'; assets.mkdir()
    manifest = {'schema': 'ali-component-master/2', 'source': str(source),
                'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'base_psd_sha256': hashlib.sha256(base.read_bytes()).hexdigest(),
                'size': [w, h], 'back_to_front': order, 'components': [],
                'native_status': 'script_generated_not_executed',
                'limitations': ['Masks are reviewed binary support, not physical alpha recovery',
                                'Inferred pixels are not recovered original artwork']}
    for c, support, observed, hidden, generated, clean, clean_support in prepared:
        files = {}
        for label, pixels, mask in [('OBSERVED', rgb, observed), ('INFERRED', generated, hidden),
                                    ('COMPLETE', np.where(observed[..., None], rgb, generated), support)]:
            target = assets / (c['id'] + '-' + label + '.png')
            Image.fromarray(np.dstack([pixels, mask.astype('uint8') * 255])).save(target)
            files[label] = str(target)
        if clean is not None:
            target = assets / (c['id'] + '-CLEAN-BASE.png')
            Image.fromarray(np.dstack([clean, clean_support.astype('uint8')])).save(target)
            files['CLEAN_BASE'] = str(target)
            if c['appearance'].get('native_mask'):
                rgb_path=assets/(c['id']+'-CLEAN-RGB.png');alpha_path=assets/(c['id']+'-PHYSICAL-ALPHA.png')
                Image.fromarray(clean).save(rgb_path);Image.fromarray(clean_support.astype('uint8')).save(alpha_path)
                files['CLEAN_RGB']=str(rgb_path);files['PHYSICAL_ALPHA']=str(alpha_path)
                if c['appearance'].get('native_source_rgb'):
                    with Image.open(path(c['appearance']['native_source_rgb'])) as native:
                        target_native=assets/(c['id']+'-NATIVE-HIRES-RGB.png');native.save(target_native)
                        files['NATIVE_HIRES_RGB']=str(target_native)
                        c['appearance']['native_source_size']=list(native.size)
            (assets / (c['id'] + '-appearance.json')).write_text(json.dumps(c['appearance'], ensure_ascii=False, indent=2))
        manifest['components'].append({'id': c['id'], 'files': files,
            'support_pixels': int(support.sum()), 'observed_pixels': int(observed.sum()),
            'inferred_pixels': int(hidden.sum()), 'provenance': c.get('provenance'),
            'environment_mask': str(path(c['environment_mask'])) if c.get('environment_mask') else None,
            'appearance': c.get('appearance'), 'clean_base_available': clean is not None,
            'transparent_holes_inside_support': 0, 'human_approved': c.get('human_approved') is True,
            'review_status': c.get('review_status', 'human_approved' if c.get('human_approved') else 'unreviewed')})
    # A new duplicate retains reviewed native text and fx. Existing groups are hidden,
    # not destroyed; independent placed assets use a full-canvas transparent frame.
    q = lambda x: json.dumps(str(x), ensure_ascii=True)
    lines = ['#target photoshop', 'var oldDialogs=app.displayDialogs,oldUnits=app.preferences.rulerUnits;try{', 'app.displayDialogs=DialogModes.NO;',
        'var base=app.open(new File('+q(base)+'));',
        'var doc=base.duplicate("Component master reconstructed");',
        'app.preferences.rulerUnits=Units.PIXELS;',
        'for(var i=0;i<doc.layerSets.length;i++){var g=doc.layerSets[i]; if(g.name.indexOf("03 Editable typography")!==0)g.visible=false;}',
        'function place(p,n,parent){var d=new ActionDescriptor();d.putPath(charIDToTypeID("null"),new File(p));executeAction(charIDToTypeID("Plc "),d,DialogModes.NO);var l=doc.activeLayer;l.name=n;l.move(parent,ElementPlacement.INSIDE);return l;}',
        'var root=doc.layerSets.add();root.name="04 Component masters - observed plus inferred";']
    lines.append(PHOTOSHOP_APPEARANCE_JS)
    lines.append(PHOTOSHOP_GRADING_JS)
    lines.append(PHOTOSHOP_MASK_JS)
    byid = {x['id']: x for x in manifest['components']}
    for name in order:
        c = byid[name]
        lines += ['var group=root.layerSets.add();group.name='+q(name)+';']
        if c['clean_base_available']:
            lines += ['var reference=group.layerSets.add();reference.name="REFERENCE - baked original appearance";']
            for label in ('INFERRED','OBSERVED'):
                if c['inferred_pixels' if label=='INFERRED' else 'observed_pixels']:
                    lines.append('place('+q(c['files'][label])+','+q(label)+',reference);')
            lines += ['reference.visible=false;',
                      'var clean=place('+q(c['files'].get('NATIVE_HIRES_RGB',c['files'].get('CLEAN_RGB',c['files']['CLEAN_BASE'])))+','+q('CLEAN BASE - '+c['appearance']['status'])+',group);',
                      ('var t='+json.dumps(c['appearance']['native_transform'])+',dim='+json.dumps(c['appearance']['native_source_size'])+';var b=clean.bounds;clean.resize(t.scale*dim[0]/(b[2].as("px")-b[0].as("px"))*100,t.scale*dim[1]/(b[3].as("px")-b[1].as("px"))*100,AnchorPosition.TOPLEFT);b=clean.bounds;clean.translate(t.tx-b[0].as("px"),t.ty-b[1].as("px"));' if 'NATIVE_HIRES_RGB' in c['files'] else ''),
                      ('nativeMask(clean,'+q(c['files']['PHYSICAL_ALPHA'])+');' if 'PHYSICAL_ALPHA' in c['files'] else ''),
                      'applyComponentEffects(clean,'+json.dumps(c['appearance'].get('effects',{}))+');',
                      'createGrade(clean,'+json.dumps(c['appearance'].get('grade_curves',{}))+');']
            if c.get('environment_mask'):
                lines.append('nativeMask(group,'+q(path(c['environment_mask']))+');')
            continue
        lines += [
                  'place('+q(c['files']['INFERRED'])+',"INFERRED - generated hidden surface",group);' if c['inferred_pixels'] else '',
                  'place('+q(c['files']['OBSERVED'])+',"OBSERVED - original visible pixels",group);' if c['observed_pixels'] else '']
    lines += ['root.move(doc.layerSets.getByName("03 Editable typography"),ElementPlacement.PLACEAFTER);',
              'var target=new File('+q(out/'component-master.psd')+');if(target.exists)throw new Error("Refusing PSD overwrite");',
              'var opts=new PhotoshopSaveOptions();opts.layers=true;doc.saveAs(target,opts,true,Extension.LOWERCASE);',
              '}finally{app.preferences.rulerUnits=oldUnits;app.displayDialogs=oldDialogs;}']
    (out/'build-photoshop.jsx').write_text('\n'.join(lines), encoding='utf-8')
    (out/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest
