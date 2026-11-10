"""Assert real on-disk inference results, separate from mocked/unit tests."""
from pathlib import Path
import json
import numpy as np
from PIL import Image
from ali.runtime import atomic_json
root=Path('jobs/v0.4');report=Path('reports/v0.4');results={}
for name in ('sam-matting','sam-small-retry'):
 r=json.loads((root/name/'result.json').read_text());pred=np.array(Image.open(r['layers'][0]['mask']))>127;truth=np.array(Image.open(root/'dataset/object-mask.png'))>127;iou=float((pred&truth).sum()/(pred|truth).sum());assert iou>.9
 results[name]={'mask_iou':iou,'backend':r['backend'],'seconds':r['elapsed_seconds'],'peak_rss_bytes':r['peak_rss_bytes'],'cpu_fallback':r.get('cpu_fallback_reason')}

def distance(a,b):
 row=list(range(len(b)+1))
 for i,ca in enumerate(a,1):
  nxt=[i]
  for j,cb in enumerate(b,1):nxt.append(min(nxt[-1]+1,row[j]+1,row[j-1]+(ca!=cb)))
  row=nxt
 return row[-1]
texts={'en':('ocr-en','Adobe Layer 2026'),'zh-TW':('ocr-zh-TW','繁體中文 圖層測試'),'zh-CN':('ocr-zh-CN-offline','简体中文 图层测试'),'ja':('ocr-ja','日本語 テスト'),'ko':('ocr-ko','한국어 테스트')}
for lang,(name,truth) in texts.items():
 r=json.loads((root/name/'result.json').read_text());text=''.join(x['text'] for x in r['records']);norm=lambda s:''.join(s.split());cer=distance(norm(truth),norm(text))/len(norm(truth));assert cer<.25
 results[name]={'language':lang,'truth':truth,'recognized':text,'cer_ignoring_whitespace':cer,'confidence':r['quality']['mean_confidence'],'seconds':r['elapsed_seconds'],'peak_rss_bytes':r['peak_rss_bytes']}
for name in ('sr-illustration','sr-photo','sr-transparent','sr-mps'):
 r=json.loads((root/name/'result.json').read_text());im=Image.open(r['image']);assert im.size==(256,192) and r['native_ai_rgb'] is True
 results[name]={'backend':r['backend'],'dimensions':list(im.size),'seconds':r['elapsed_seconds'],'peak_rss_bytes':r['peak_rss_bytes']}
 if name=='sr-transparent':
  source=Image.open(root/'dataset/sr-transparent.png');expected=source.getchannel('A').resize(im.size,Image.Resampling.BILINEAR);assert np.array_equal(expected,im.getchannel('A'));results[name]['alpha_coverage_exact']=True
font=json.loads((root/'font-match-v2/result.json').read_text());assert len(font['top5'])==5 and font['top5'][0]['weight']==400
results['font-match']={'top1':{k:v for k,v in font['top5'][0].items() if k in ('family','weight','similarity','spacing_at_48px')},'count':len(font['top5'])}
for app,suffix in [('photoshop','psd'),('illustrator','ai'),('indesign','indd')]:
 p=root/'scene-objects'/app/f'page_001.{suffix}';assert p.is_file() and p.stat().st_size>1000
 results[app]={'native_file':str(p),'bytes':p.stat().st_size,'reopened_in_application':True,'evidence':str(report/('adobe-'+{'photoshop':'ps','illustrator':'ai','indesign':'id'}[app]+'-native.txt'))}
for app,expected in [('ps','layers=3'),('ai','items=2'),('id','items=2')]:
 evidence=report/f'adobe-{app}-native.txt';assert expected in evidence.read_text()
text_evidence=(report/'adobe-editable-text-native.txt').read_text();assert 'textFrames=1' in text_evidence and 'overflow=false' in text_evidence
results['indesign-native-text']={'native_file':'jobs/v0.4/scene-native-text/indesign/page_001.indd','evidence':text_evidence.strip(),'scope':'synthetic fixed-font fixture; not newspaper reconstruction'}
scene=json.loads((root/'scene-newspaper/scene.json').read_text());assert scene['status']=='complete' 
results['pdf-native-text']={'boards':len(scene['boards']),'text_nodes':sum(n['type']=='text' for b in scene['boards'] for n in b['nodes']),'ocr_skipped_all':all('skipped' in b['quality']['ocr'] for b in scene['boards'])}
results['baseline-v03']={'raster_input_semantic_objects':0,'raster_ocr_regions':0,'ai_super_resolution':False,'newspaper_indd':'four flat placed pages; not editable reconstruction'}
atomic_json(report/'integration-results.json',results);print(json.dumps(results,ensure_ascii=False,indent=2))
