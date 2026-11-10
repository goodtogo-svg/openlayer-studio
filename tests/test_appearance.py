import copy
import pytest
from ali.appearance import validate_appearance


def appearance():
    return {'human_approved':True,'clean_image':'clean.png','support_mask':'support.png','status':'estimated_clean','provenance':'reviewed reconstruction','effects':{'outer_glow':{'enabled':True,'rgb':[255,150,30],'opacity':55,'size_px':18}}}


def test_parameters_and_original_claims():
    a=appearance();assert validate_appearance(a)==a
    a['status']='provided_original'
    with pytest.raises(ValueError,match='evidence'):validate_appearance(a)
    a['original_evidence']='User supplied layered source';validate_appearance(a)


@pytest.mark.parametrize('field,value',[('size_px',float('nan')),('opacity',101),('enabled','true'),('rgb',[0,0,999]),('unimplemented_bevel',2)])
def test_reject_invalid_and_unknown_parameters(field,value):
    a=appearance();a['effects']['outer_glow'][field]=value
    with pytest.raises(ValueError):validate_appearance(a)
