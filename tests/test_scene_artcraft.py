import base64,io,json
from pathlib import Path
import pytest
from PIL import Image
from ali.runtime import atomic_json
from ali.scene_artcraft import choose_canvas,generate_artcraft

def _png(path,size,color):
    Image.new('RGBA',size,color).save(path);return str(path)

def _scene(tmp_path,nodes,w=400,h=600):
    p=tmp_path/'scene.json';atomic_json(p,{'schema':'ali-scene/1','boards':[{'id':'b','width':w,'height':h,'width_pt':w,'height_pt':h,'nodes':nodes}]});return p

def test_choose_canvas_matches_artcraft_aspect_boxes():
    assert choose_canvas(400,600)==('2:3',(683,1024))
    assert choose_canvas(1920,1080)==('3:2',(1024,683))
    assert choose_canvas(500,500)==('1:1',(1024,1024))

def test_raster_layers_export_in_z_order_with_importable_fields(tmp_path):
    bg=_png(tmp_path/'bg.png',(400,600),(255,0,0,255));fg=_png(tmp_path/'fg.png',(400,600),(0,0,255,128))
    nodes=[{'id':'fg','type':'raster','asset':fg,'visible':True,'z_index':2},{'id':'bg','type':'raster','asset':bg,'visible':True,'z_index':0},
           {'id':'ref','type':'raster','asset':bg,'visible':False,'z_index':1},{'id':'t','type':'text','text':'hi','visible':True,'z_index':100}]
    job=generate_artcraft(_scene(tmp_path,nodes),tmp_path/'out')[0]
    data=json.loads(Path(job['output']).read_text())
    assert data['version']=='2.0' and data['aspectRatioType']=='2:3' and data['inpaintLineNodes']==[]
    assert [n['id'] for n in data['drawNodes']]==['ali-bg','ali-fg']
    n=data['drawNodes'][1]
    for key in ('id','x','y','width','height','fill','type','stroke','strokeWidth','draggable','rotation','scaleX','scaleY','offsetX','offsetY','locked'):assert key in n
    assert n['type']=='image' and n['imageDataUrl'].startswith('data:image/png;base64,')
    img=Image.open(io.BytesIO(base64.b64decode(n['imageDataUrl'].split(',',1)[1])))
    assert img.size==(n['width'],n['height'])==(400,600) and img.getpixel((5,5))[3]==128
    assert job['skipped_nodes'][0]['id']=='t' and job['scale']==1.0
    handoff=json.loads((tmp_path/'out/artcraft-handoff.json').read_text())
    assert handoff['artcraft_verified'] is False

def test_large_board_is_downscaled_and_centered(tmp_path):
    a=_png(tmp_path/'a.png',(3000,3000),(0,255,0,255))
    job=generate_artcraft(_scene(tmp_path,[{'id':'a','type':'raster','asset':a,'visible':True}],3000,3000),tmp_path/'out')[0]
    n=json.loads(Path(job['output']).read_text())['drawNodes'][0]
    assert (n['x'],n['y'],n['width'],n['height'])==(0,0,1024,1024) and job['letterboxed'] is False

def test_bbox_layer_placement_and_refuse_overwrite(tmp_path):
    a=_png(tmp_path/'a.png',(100,50),(0,0,0,255))
    p=_scene(tmp_path,[{'id':'a','type':'raster','asset':a,'visible':True,'bbox':[100,200,200,250]}])
    n=json.loads(Path(generate_artcraft(p,tmp_path/'out')[0]['output']).read_text())['drawNodes'][0]
    assert (n['width'],n['height'])==(100,50) and n['y']==200+212  # (1024-600)/2 vertical letterbox
    with pytest.raises(FileExistsError):generate_artcraft(p,tmp_path/'out')

def test_unsized_raster_without_bbox_is_rejected(tmp_path):
    a=_png(tmp_path/'a.png',(10,10),(0,0,0,255))
    with pytest.raises(ValueError,match='bbox'):generate_artcraft(_scene(tmp_path,[{'id':'a','type':'raster','asset':a,'visible':True}]),tmp_path/'out')
