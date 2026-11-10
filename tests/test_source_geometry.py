import numpy as np
import pytest
from PIL import Image
from ali.source_geometry import doorway_masks,neutralize_person,texture_plane
from ali.grading import validate_curves


def test_original_pose_alpha_retained_without_warp():
    a=Image.fromarray(np.array([[0,0,255,0],[0,255,255,0],[0,0,255,255]],dtype='uint8'))
    source=Image.new('RGB',a.size,(250,230,210));out=neutralize_person(source,a)
    assert np.array_equal(np.array(out)[:,:,3],np.array(a))


def test_door_ring_and_opening_do_not_overlap():
    inner=[[15,25],[15,10],[35,10],[35,25]];outer=[[10,25],[10,2],[40,2],[40,25]]
    opening,ring=doorway_masks((50,45),outer,inner,40)
    x,y=np.array(opening),np.array(ring)
    assert x[32,25]==255 and y[32,25]==0 and x[0,0]==0
    assert not ((x==255)&(y==255)).any()


def test_degenerate_plane_and_invalid_grade_rejected():
    with pytest.raises(ValueError):texture_plane(Image.new('RGB',(8,8)),(30,30),[[1,1]]*4,[60,70,130])
    with pytest.raises(ValueError):validate_curves({'composite':[[0,0],[140,130],[90,80],[255,255]]})
    validate_curves({'composite':[[0,0],[128,140],[255,255]]})


def test_native_bevel_range_validation():
    from ali.native_styles import validate_bevel
    params={'size_px':4,'soften_px':.8,'depth_percent':120,'light_angle':115,'altitude':35,'highlight_opacity':78,'shadow_opacity':35,'highlight_rgb':[255,248,211],'shadow_rgb':[120,63,24]}
    validate_bevel(params)
    with pytest.raises(ValueError):validate_bevel({**params,'size_px':float('nan')})
