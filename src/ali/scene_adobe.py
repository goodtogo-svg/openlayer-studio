"""Scene handoff: reviewed text can become native type; no implicit approval."""
import json
import math
from pathlib import Path
from ali.runtime import atomic_json

def js(value):return json.dumps(value,ensure_ascii=True)

def generate_scene(scene_path,app,out):
 if app not in ('photoshop','illustrator','indesign'):raise ValueError('Unsupported app')
 scene=json.loads(Path(scene_path).read_text());out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True);jobs=[]
 if scene['schema']!='ali-scene/1':raise ValueError('Invalid scene schema')
 for i,b in enumerate(scene['boards'],1):
  suffix={'photoshop':'psd','illustrator':'ai','indesign':'indd'}[app];target=out/f'page_{i:03d}.{suffix}';scriptfile=out/f'page_{i:03d}_{app}.jsx'
  if target.exists() or scriptfile.exists():raise FileExistsError('Refusing to overwrite scene export')
  nodes=sorted([n for n in b['nodes'] if n.get('visible',False)],key=lambda n:n.get('z_index',0))
  if app=='photoshop' and any(n['type']=='vector' for n in nodes):raise ValueError('Photoshop scene vector requires an explicit raster derivative node')
  w=b['width_pt'];h=b['height_pt'];sx=w/b['width'];sy=h/b['height']
  resolution=72.0/sx
  if app=='photoshop' and (not math.isfinite(resolution) or resolution<=0 or abs(sx-sy)>max(sx,sy)*0.001):raise ValueError('Inconsistent pixel aspect or physical page dimensions')
  lines=[f'#target {app}',f'if(File({js(str(target))}).exists) throw new Error("Output exists");']
  if app=='indesign':lines+=['var d=app.documents.add();','d.documentPreferences.facingPages=false;',f'd.documentPreferences.pageWidth={js(str(w)+"pt")};',f'd.documentPreferences.pageHeight={js(str(h)+"pt")};','d.viewPreferences.horizontalMeasurementUnits=MeasurementUnits.POINTS;d.viewPreferences.verticalMeasurementUnits=MeasurementUnits.POINTS;d.viewPreferences.rulerOrigin=RulerOrigin.PAGE_ORIGIN;d.zeroPoint=[0,0];','try {']
  elif app=='illustrator':lines+=[f'var d=app.documents.add(DocumentColorSpace.RGB,{w},{h});','try {']
  else:lines+=[f'var d=app.documents.add(UnitValue({b["width"]},"px"),UnitValue({b["height"]},"px"),{resolution},"ALI_SCENE",NewDocumentMode.RGB,DocumentFill.TRANSPARENT);','try {']
  for node in nodes:
   if node['type'] in ('raster','vector'):
    path=Path(node['asset']).resolve()
    if not path.is_file():raise FileNotFoundError(path)
    if app=='indesign':lines+=['var f=d.pages.item(0).rectangles.add();',f'f.geometricBounds=["0pt","0pt",{js(str(h)+"pt")},{js(str(w)+"pt")}];f.strokeWeight=0;',f'f.place(File({js(str(path))}));f.fit(FitOptions.PROPORTIONALLY);f.fit(FitOptions.CENTER_CONTENT);']
    elif app=='illustrator':lines+=['var f=d.placedItems.add();',f'f.file=File({js(str(path))});f.position=[0,{h}];f.width={w};f.height={h};']
    elif node['type']=='raster':lines+=[f'var s=app.open(File({js(str(path))}));var l=s.activeLayer.duplicate(d,ElementPlacement.PLACEATBEGINNING);s.close(SaveOptions.DONOTSAVECHANGES);l.name={js(node["id"])};']
   elif node['type']=='text':
    if not node.get('approved_font') or not node.get('human_approved'):raise ValueError('Visible text needs human_approved and approved_font')
    bounds=node.get('bounds')
    if bounds is None:
     points=node['polygon'];bounds=[min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]
    x,y,x2,y2=bounds
    if node.get('coordinate_space')=='pixels':x*=sx;x2*=sx;y*=sy;y2*=sy
    size=node.get('font_size',max(1,(y2-y)*.75))
    if app=='indesign':lines+=['var t=d.pages.item(0).textFrames.add();',f't.geometricBounds=[{y},{x},{y2},{x2}];t.contents={js(node["text"])};',f't.parentStory.appliedFont={js(node["approved_font"])};t.parentStory.pointSize={size};']
    elif app=='illustrator':lines+=['var t=d.textFrames.add();',f't.contents={js(node["text"])};t.position=[{x},{h-y}];t.textRange.characterAttributes.size={size};t.textRange.characterAttributes.textFont=app.textFonts.getByName({js(node["approved_font"])});']
    else:lines+=['var t=d.artLayers.add();t.kind=LayerKind.TEXT;',f't.textItem.contents={js(node["text"])};t.textItem.font={js(node["approved_font"])};t.textItem.size=UnitValue({size},"pt");t.textItem.position=[UnitValue({x/sx},"px"),UnitValue({y/sy},"px")];']
  if app=='indesign':lines+=[f'd.save(File({js(str(target))}));','} finally {d.close(SaveOptions.NO);}']
  elif app=='illustrator':lines+=[f'd.saveAs(File({js(str(target))}),new IllustratorSaveOptions());','} finally {d.close(SaveOptions.DONOTSAVECHANGES);}']
  else:lines+=['var opt=new PhotoshopSaveOptions();opt.layers=true;',f'd.saveAs(File({js(str(target))}),opt,true,Extension.LOWERCASE);','} finally {d.close(SaveOptions.DONOTSAVECHANGES);}']
  scriptfile.write_text('\n'.join(lines),encoding='utf-8');jobs.append({'script':str(scriptfile),'expected_output':str(target),'status':'requires_adobe_execution'})
 atomic_json(out/'handoff.json',{'scene':str(Path(scene_path).resolve()),'jobs':jobs,'native_verified':False});return jobs
