import numpy as np
import pytest
from PIL import Image
from ali.asset_registration import enclosed_opening,anchored_transform,map_point,place_uniform


def test_enclosed_opening_independent_of_exterior():
    a=np.zeros((30,40),dtype='uint8');a[3:28,5:35]=255;a[7:24,12:28]=0
    h=np.array(enclosed_opening(Image.fromarray(a),(18,16)))
    assert h[16,18]==255 and h[0,0]==0 and h.sum()==17*16*255
    with pytest.raises(ValueError):enclosed_opening(Image.fromarray(a),(0,0))
    with pytest.raises(ValueError):enclosed_opening(Image.fromarray(a),(6,5))


def test_uniform_asset_is_not_anisotropically_stretched():
    t=anchored_transform(10,90,40,20,180,100)
    assert t=={'scale':2.0,'tx':20.0,'ty':0.0}
    assert map_point([40,10],t)==[100,20] and map_point([40,90],t)==[100,180]
    a=Image.new('RGBA',(10,10),(0,0,0,0));a.paste((120,150,200,255),(3,2,7,8))
    out=place_uniform(a,(40,40),{'scale':2,'tx':4,'ty':6});pixels=np.array(out);y,x=np.where(pixels[:,:,3]>=128)
    # Bicubic has low-alpha support beyond the edge; register the physical core.
    assert (x.min(),y.min(),x.max()+1,y.max()+1)==(10,10,18,22)
    assert pixels[16,14].tolist()==[120,150,200,255]
