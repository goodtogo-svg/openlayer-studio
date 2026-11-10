import json
import numpy as np
import pytest
from PIL import Image
from ali.scene_effects import render_layer,validate,compile_effects


def layer(**kw):
    return {'id':'stars','owner':'portal','kind':'particles','blend':'screen','opacity':70,'blur_px':0,'rgb':[255,180,90],'box':[5,5,55,35],'count':30,'radius_px':1,'seed':47,**kw}


def test_deterministic_reproduction_and_seed_changes(tmp_path):
    a=layer();one=np.array(render_layer(a,[64,40],tmp_path));two=np.array(render_layer(a,[64,40],tmp_path))
    assert np.array_equal(one,two)
    assert not np.array_equal(one,np.array(render_layer(layer(seed=48),[64,40],tmp_path)))
    assert one[:,:,3].max()>0 and one[0,0,3]==0


def test_arch_transparency_and_gradient(tmp_path):
    a=layer(kind='arch',box=[20,5,40,36],gradient_rgb=[[80,20,100],[255,230,180]])
    x=np.array(render_layer(a,[64,40],tmp_path))
    assert x[15,30,3]>240 and x[0,0,3]==0 and x[37,30,3]<10
    assert x[32,30,0]>x[15,30,0]


@pytest.mark.parametrize('change',[{'opacity':101},{'blur_px':float('nan')},{'kind':'fake-ai'},{'count':2001},{'seed':-1},{'blend':'unknown'}])
def test_invalid_settings_rejected(change):
    with pytest.raises(ValueError):validate({'size':[64,40],'layers':[layer(**change)]})


def test_provenance_and_refuse_overwrite(tmp_path):
    (tmp_path/'base.psd').write_bytes(b'fixture; no native claim')
    p=tmp_path/'plan.json';p.write_text(json.dumps({'size':[64,40],'base_psd':'base.psd','layers':[layer()]}))
    out=tmp_path/'job';r=compile_effects(p,out)
    assert r['status']=='script_generated_not_executed' and r['is_original'] is False
    assert (out/'assets/stars.png').exists()
    with pytest.raises(ValueError):compile_effects(p,out)


def test_repeat_build_replaces_only_effect_groups_in_duplicate(tmp_path):
    (tmp_path/'base.psd').write_bytes(b'fixture')
    p=tmp_path/'plan.json';p.write_text(json.dumps({'size':[64,40],'base_psd':'base.psd','layers':[layer()]}))
    compile_effects(p,tmp_path/'job')
    jsx=(tmp_path/'job/build-photoshop.jsx').read_text()
    assert 'base.duplicate(' in jsx
    assert 'p.layerSets[j].name==="PARAMETRIC FX - "+ids[i]' in jsx
    assert 'p.layerSets[j].remove()' in jsx


def test_post_blur_clip_and_exclude_are_strict(tmp_path):
    a=np.zeros((40,64),dtype='uint8');a[:,20:40]=255;Image.fromarray(a).save(tmp_path/'clip.png')
    effect=layer(kind='ellipse',box=[0,0,64,40],blur_px=4,clip_mask='clip.png')
    result=np.array(render_layer(effect,[64,40],tmp_path))[:,:,3]
    assert result[:,0:20].max()==0 and result[:,40:].max()==0 and result[:,24:36].max()>0
    effect.pop('clip_mask');effect['exclude_mask']='clip.png';result=np.array(render_layer(effect,[64,40],tmp_path))[:,:,3]
    assert result[:,20:40].max()==0


def test_directional_rim_faces_light(tmp_path):
    a=np.zeros((40,64),dtype='uint8');a[8:32,20:44]=255;Image.fromarray(a).save(tmp_path/'physical.png')
    effect=layer(kind='rim',width_px=2,alpha_mask='physical.png',light_position=[60,20],ambient_floor=0)
    result=np.array(render_layer(effect,[64,40],tmp_path))[:,:,3]
    assert result[14:27,42:44].sum()>result[14:27,20:22].sum()*3


def test_native_constraints_store_unclipped_plate_and_editable_mask(tmp_path):
    a=np.zeros((40,64),dtype='uint8');a[:,20:40]=255;Image.fromarray(a).save(tmp_path/'clip.png');(tmp_path/'base.psd').write_bytes(b'fixture')
    effect=layer(kind='ellipse',box=[0,0,64,40],clip_mask='clip.png')
    p=tmp_path/'plan.json';p.write_text(json.dumps({'size':[64,40],'base_psd':'base.psd','native_constraints':True,'layers':[effect]}));r=compile_effects(p,tmp_path/'job')
    assert 'native_mask_asset' in r['layers'][0]
    raw=np.array(Image.open(tmp_path/'job/assets/stars.png'))[:,:,3];mask=np.array(Image.open(tmp_path/'job/assets/stars-NATIVE-MASK.png'))
    assert raw[:,0:20].max()>0 and mask[:,0:20].max()==0
    assert 'nativeMask(fx,' in (tmp_path/'job/build-photoshop.jsx').read_text()


def test_effect_only_profiles_are_validated_and_preserved(tmp_path):
    (tmp_path/'base.psd').write_bytes(b'fixture')
    grade={'portal':{'composite':[[0,0],[128,140],[255,255]]}}
    fx={'key':{'outer_glow':{'enabled':True,'rgb':[255,160,90],'opacity':56,'size_px':17}}}
    p=tmp_path/'plan.json';p.write_text(json.dumps({'size':[64,40],'base_psd':'base.psd','layers':[layer()],'component_grading':grade,'component_fx':fx}))
    r=compile_effects(p,tmp_path/'job');assert r['component_grading']==grade and r['component_fx']==fx
    jsx=(tmp_path/'job/build-photoshop.jsx').read_text();assert 'createGrade(clean,' in jsx and 'applyComponentEffects(clean,' in jsx
    assert 'clean.resize(' not in jsx and 'clean.translate(' not in jsx and 'nativeMask(clean,' not in jsx


@pytest.mark.parametrize('config',[{'component_grading':{'unknown':{}}},{'component_grading':{'portal':{'composite':[[0,0],[255,999]]}}},{'component_fx':{'key':{'outer_glow':{'enabled':True,'rgb':[255,160,90],'opacity':101,'size_px':17}}}}])
def test_invalid_filter_profile_rejected_before_output(tmp_path,config):
    (tmp_path/'base.psd').write_bytes(b'fixture');p=tmp_path/'plan.json';p.write_text(json.dumps({'size':[64,40],'base_psd':'base.psd','layers':[layer()],**config}))
    with pytest.raises(ValueError):compile_effects(p,tmp_path/'job')
    assert not (tmp_path/'job').exists()


def test_bloom_intensity_recovers_energy_without_escaping_strict_clip(tmp_path):
    a=np.zeros((40,64),dtype='uint8');a[:,20:40]=255;Image.fromarray(a).save(tmp_path/'clip.png')
    effect=layer(kind='stars',stars=[[30,20,9,0]],blur_px=5,clip_mask='clip.png',clip_stage='post_blur')
    low=np.array(render_layer(effect,[64,40],tmp_path))[:,:,3]
    bright=np.array(render_layer({**effect,'intensity':5},[64,40],tmp_path))[:,:,3]
    assert bright.max()>low.max()*2 and bright[:,0:20].max()==0 and bright[:,40:].max()==0
    with pytest.raises(ValueError):validate({'size':[64,40],'layers':[layer(intensity=17)]})


def test_environment_light_is_separate_from_component_and_background(tmp_path):
    (tmp_path/'base.psd').write_bytes(b'fixture');p=tmp_path/'plan.json';p.write_text(json.dumps({'size':[64,40],'base_psd':'base.psd','layers':[layer(owner='environment')]}));compile_effects(p,tmp_path/'job')
    jsx=(tmp_path/'job/build-photoshop.jsx').read_text();assert 'name="ENVIRONMENT FX"' in jsx and 'root.layerSets.getByName("background"),ElementPlacement.PLACEBEFORE' in jsx


def test_color_reflection_uses_native_color_blend(tmp_path):
    (tmp_path/'base.psd').write_bytes(b'fixture');p=tmp_path/'plan.json';p.write_text(json.dumps({'size':[64,40],'base_psd':'base.psd','layers':[layer(blend='color')]}));r=compile_effects(p,tmp_path/'job')
    assert r['layers'][0]['blend']=='color'
    assert 'BlendMode.COLORBLEND;' in (tmp_path/'job/build-photoshop.jsx').read_text()
