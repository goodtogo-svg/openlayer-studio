"""Deterministic synthetic evaluation data; never use private designs as training data."""
from pathlib import Path
import json
from PIL import Image,ImageDraw,ImageFont
root=Path('jobs/v0.4/dataset');root.mkdir(parents=True,exist_ok=True)
im=Image.new('RGB',(320,240),'#f5f0e0');d=ImageDraw.Draw(im);d.rectangle((30,40,130,180),fill='#1650a0');d.ellipse((170,50,290,170),fill='#ef9130');im.save(root/'objects.png')
mask=Image.new('L',im.size);ImageDraw.Draw(mask).rectangle((30,40,130,180),fill=255);mask.save(root/'object-mask.png')
(root/'prompts.json').write_text(json.dumps({'schema':'ali-prompts/1','coordinate_space':'source_pixels','objects':[{'id':'rectangle','points':[[80,100]],'labels':[1],'box':[25,35,135,185],'corrections':[]}]}))
font=ImageFont.truetype('models/fonts/Inter.ttf',36);im=Image.new('RGB',(440,100),'white');ImageDraw.Draw(im).text((16,25),'Adobe Layer 2026',font=font,fill='black');im.save(root/'english.png')
font=ImageFont.truetype('models/fonts/NotoSansTC.ttf',36);im=Image.new('RGB',(440,100),'white');ImageDraw.Draw(im).text((16,20),'繁體中文 圖層測試',font=font,fill='black');im.save(root/'zh-TW.png')
im=Image.open(root/'objects.png').resize((64,48));im.save(root/'sr-lowres.png');im=im.convert('RGBA');alpha=Image.new('L',im.size);ImageDraw.Draw(alpha).ellipse((4,4,58,42),fill=180);im.putalpha(alpha);im.save(root/'sr-transparent.png')
(root/'truth.json').write_text(json.dumps({'schema':'ali-benchmark/1','license':'CC0 synthetic geometry and author-created text','ocr':{'english.png':'Adobe Layer 2026','zh-TW.png':'繁體中文 圖層測試'},'segmentation':{'objects.png':'object-mask.png'},'sr':'dimensions/alpha only; no high-res photographic ground truth'},ensure_ascii=False,indent=2))

# Additional language fixtures require verified coverage, not tofu glyphs.
from fontTools.ttLib import TTFont
for language,text,fontname in [('zh-CN','简体中文 图层测试','NotoSansSC.ttf'),('ja','日本語 テスト','NotoSansTC.ttf'),('ko','한국어 테스트','NotoSansKR.ttf')]:
 fontpath=Path('models/fonts')/fontname
 with TTFont(fontpath) as face:
  if any(ord(c) not in face.getBestCmap() for c in text if not c.isspace()):raise ValueError('Fixture font lacks glyphs: '+language)
 image=Image.new('RGB',(440,100),'white');ImageDraw.Draw(image).text((16,20),text,font=ImageFont.truetype(str(fontpath),36),fill='black');image.save(root/(language+'.png'))
 truth=json.loads((root/'truth.json').read_text());truth['ocr'][language+'.png']=text;(root/'truth.json').write_text(json.dumps(truth,ensure_ascii=False,indent=2))
