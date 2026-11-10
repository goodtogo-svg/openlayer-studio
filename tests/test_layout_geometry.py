import numpy as np
import pytest
from PIL import Image
from ali.layout_geometry import warp,point,validate_axis


def test_knots_shared_by_geometry_and_light_anchors():
    g={'x_source':[0,4,12],'x_target':[0,6,12],'y_source':[0,8],'y_target':[0,8]}
    assert point([4,3],g)==[6,3]
    a=np.zeros((8,12,4),dtype='uint8');a[:,4:9]=[200,80,30,255]
    result=np.array(warp(Image.fromarray(a),g))
    assert result[4,7,3]==255 and result[4,3,3]==0
    assert result[4,7,0]==200


def test_folds_and_invalid_span_rejected():
    with pytest.raises(ValueError):validate_axis([0,4,12],[0,8,7],12)
    with pytest.raises(ValueError):validate_axis([0,4,12],[0,4,4,12],12)
