import json
import numpy as np
import pytest
from PIL import Image
from ali.component_master import compile_master


def fixture(tmp_path):
    Image.new('RGB',(12,8),(8,30,80)).save(tmp_path/'source.png')
    Image.new('RGB',(12,8),(90,40,10)).save(tmp_path/'repair.png')
    Image.fromarray(np.full((8,12),255,dtype='uint8')).save(tmp_path/'support.png')
    a=np.full((8,12),255,dtype='uint8');a[:,6:]=0
    Image.fromarray(a).save(tmp_path/'observed.png')
    (tmp_path/'base.psd').write_bytes(b'fixture only, no native output claim')
    p={'source':'source.png','base_psd':'base.psd','components':[{'id':'wall','support_mask':'support.png','observed_mask':'observed.png','inferred_image':'repair.png','provenance':'test inferred','human_approved':True}]}
    plan=tmp_path/'plan.json';plan.write_text(json.dumps(p));return plan,p


def test_observed_conservation_and_complete_support(tmp_path):
    plan,p=fixture(tmp_path);out=tmp_path/'out';r=compile_master(plan,out)
    a=np.array(Image.open(out/'assets/wall-COMPLETE.png'))
    assert (a[:,:6,:3]==[8,30,80]).all()
    assert (a[:,6:,:3]==[90,40,10]).all()
    assert (a[:,:,3]==255).all()
    assert r['native_status']=='script_generated_not_executed'
    with pytest.raises(ValueError):compile_master(plan,out)


def test_unreviewed_and_missing_provenance_fail_before_output(tmp_path):
    plan,p=fixture(tmp_path);p['components'][0]['human_approved']=False;plan.write_text(json.dumps(p))
    with pytest.raises(ValueError):compile_master(plan,tmp_path/'out')
    assert not (tmp_path/'out').exists()
    p['components'][0]['human_approved']=True;p['components'][0].pop('provenance');plan.write_text(json.dumps(p))
    with pytest.raises(ValueError):compile_master(plan,tmp_path/'out')


def test_clean_base_is_not_baked_observed_pixels(tmp_path):
    plan,p=fixture(tmp_path)
    p['components'][0]['appearance']={'human_approved':True,'clean_image':'repair.png','support_mask':'support.png','status':'estimated_clean','provenance':'synthetic test only','effects':{'outer_glow':{'enabled':True,'rgb':[255,120,30],'opacity':45,'size_px':12}}}
    plan.write_text(json.dumps(p));out=tmp_path/'clean';r=compile_master(plan,out)
    clean=np.array(Image.open(out/'assets/wall-CLEAN-BASE.png'))
    assert (clean[:,:,:3]==[90,40,10]).all()
    assert (np.array(Image.open(out/'assets/wall-OBSERVED.png'))[:,:6,:3]==[8,30,80]).all()
    assert r['components'][0]['appearance']['status']=='estimated_clean'
    jsx=(out/'build-photoshop.jsx').read_text()
    assert 'reference.visible=false' in jsx and 'applyComponentEffects(clean,' in jsx


def test_agent_review_is_not_human_approval_and_soft_alpha_survives(tmp_path):
    plan,p=fixture(tmp_path)
    alpha=np.full((8,12),255,dtype='uint8');alpha[:,0]=96
    Image.fromarray(alpha).save(tmp_path/'soft.png')
    c=p['components'][0];c['human_approved']=False;c['review_status']='agent_reviewed';c['review_notes']='Test candidate inspected; user acceptance pending'
    c['appearance']={'human_approved':False,'review_status':'agent_reviewed','review_notes':'Test image only','clean_image':'repair.png','support_mask':'soft.png','status':'estimated_clean','provenance':'synthetic test only','effects':{}}
    plan.write_text(json.dumps(p));out=tmp_path/'soft-out';r=compile_master(plan,out)
    result=r['components'][0]
    assert result['human_approved'] is False and result['review_status']=='agent_reviewed'
    assert np.array_equal(np.array(Image.open(out/'assets/wall-CLEAN-BASE.png'))[:,:,3],alpha)
    assert 'finally{app.preferences.rulerUnits=oldUnits' in (out/'build-photoshop.jsx').read_text()


def test_native_mask_and_clipped_grade_assets(tmp_path):
    plan,p=fixture(tmp_path);c=p['components'][0]
    c['appearance']={'human_approved':True,'clean_image':'repair.png','support_mask':'support.png','status':'estimated_clean','provenance':'test only','native_mask':True,'grade_curves':{'composite':[[0,0],[128,140],[255,255]]},'effects':{}}
    plan.write_text(json.dumps(p));out=tmp_path/'native';r=compile_master(plan,out);files=r['components'][0]['files']
    assert Image.open(files['CLEAN_RGB']).mode=='RGB' and Image.open(files['PHYSICAL_ALPHA']).mode=='L'
    jsx=(out/'build-photoshop.jsx').read_text();assert 'nativeMask(clean,' in jsx and 'createGrade(clean,' in jsx
    assert 'doc.activeLayer.grouped=true' in jsx


def test_environment_mask_is_separate_from_physical_body(tmp_path):
    plan,p=fixture(tmp_path);c=p['components'][0]
    c['environment_mask']='observed.png'
    c['appearance']={'human_approved':True,'clean_image':'repair.png','support_mask':'support.png','status':'estimated_clean','provenance':'fixture only','native_mask':True,'effects':{}}
    plan.write_text(json.dumps(p));out=tmp_path/'environment';r=compile_master(plan,out)
    assert np.array(Image.open(r['components'][0]['files']['CLEAN_BASE']))[:,:,3].min()==255
    assert 'nativeMask(group,' in (out/'build-photoshop.jsx').read_text()


def test_native_high_resolution_source_uses_transform_before_mask(tmp_path):
    plan,p=fixture(tmp_path);Image.new('RGB',(24,16),(90,40,10)).save(tmp_path/'highres.png')
    p['components'][0]['appearance']={'human_approved':True,'clean_image':'repair.png','support_mask':'support.png','status':'estimated_clean','provenance':'fixture','native_mask':True,'native_source_rgb':'highres.png','native_transform':{'scale':.5,'tx':0,'ty':0},'effects':{}}
    plan.write_text(json.dumps(p));r=compile_master(plan,tmp_path/'native-hires')
    assert Image.open(r['components'][0]['files']['NATIVE_HIRES_RGB']).size==(24,16)
    assert r['components'][0]['appearance']['native_source_size']==[24,16]
    jsx=(tmp_path/'native-hires/build-photoshop.jsx').read_text()
    assert jsx.index('clean.resize(')<jsx.index('nativeMask(clean,')
