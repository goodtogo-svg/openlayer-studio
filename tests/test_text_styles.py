import pytest
from PIL import Image,ImageDraw,ImageFilter
from ali.text_styles import estimate_style


def test_white_label_does_not_invent_gold_gradient_or_glow():
    im=Image.new('RGB',(160,70),(5,12,55));ImageDraw.Draw(im).rectangle((30,20,125,47),fill='white')
    s=estimate_style(im,[0,0,160,70])
    assert s['fill_rgb']==[255,255,255]
    assert not s['gradient']['enabled'] and not s['outer_glow']['enabled']
    assert not s['original_parameters_recovered']


def test_warm_halo_estimate_and_invalid_region_are_explicit():
    mask=Image.new('L',(160,80));ImageDraw.Draw(mask).rectangle((30,28,125,50),fill=255)
    im=Image.new('RGB',mask.size,(3,8,40));im.paste((190,72,40),(0,0),mask.filter(ImageFilter.GaussianBlur(8)));im.paste((255,224,160),(0,0),mask)
    s=estimate_style(im,[0,0,160,80]);assert s['outer_glow']['enabled'] and s['needs_visual_review']
    with pytest.raises(ValueError):estimate_style(im,[-1,0,50,60])
    with pytest.raises(ValueError):estimate_style(Image.new('RGB',(20,20),'black'),[0,0,20,20])
