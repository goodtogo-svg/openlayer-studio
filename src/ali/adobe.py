# Generate operator-reviewable Adobe JSX jobs; never claim it ran.
from pathlib import Path
import json

def esc(s):return str(s).replace('\\','\\\\').replace('"','\\"')
def generate(manifest_path, app, output_dir):
    manifest_path=Path(manifest_path).resolve();data=json.loads(manifest_path.read_text(encoding='utf-8'))
    out=Path(output_dir).resolve();out.mkdir(parents=True,exist_ok=True)
    if app not in ('photoshop','illustrator','indesign'):raise ValueError('app must be photoshop/illustrator/indesign')
    jobs=[]
    for n,board in enumerate(data['artboards'],1):
        stem='page_%03d'%n
        if app=='photoshop':
            target=out/(stem+'.psd')
            # Photoshop legacy ExtendScript, operator triggers File > Scripts > Browse; supports per-layer raster assets.
            script='''#target photoshop
app.displayDialogs = DialogModes.NO;
if (File("TARGET").exists) throw new Error("Output exists; refusing overwrite");
var doc = app.documents.add(WIDTH, HEIGHT, 72, "ALI_LAYERED", NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
LAYERS
var opt = new PhotoshopSaveOptions(); opt.layers = true;
doc.saveAs(new File("TARGET"), opt, true, Extension.LOWERCASE);
doc.close(SaveOptions.DONOTSAVECHANGES);
'''.replace('WIDTH',str(board['width'])).replace('HEIGHT',str(board['height'])).replace('TARGET',esc(target))
            items=[]
            for layer in reversed(board['layers']):
                x,y=int(layer.get('x',0)),int(layer.get('y',0))
                items.append('''var src=app.open(new File("%s")); var cp=src.activeLayer.duplicate(doc, ElementPlacement.PLACEATBEGINNING); src.close(SaveOptions.DONOTSAVECHANGES); cp.name="%s"; cp.translate(UnitValue(%d,"px"),UnitValue(%d,"px"));\n'''%(esc(layer['image']),esc(layer['name']),x,y))
            script=script.replace('LAYERS',''.join(items))
        elif app=='illustrator':
            target=out/(stem+'.ai')
            svg=board.get('svg')
            if not svg:continue
            script='''#target illustrator
if (File("TARGET").exists) throw new Error("Output exists; refusing overwrite");
var src = app.open(new File("SOURCE"));
var opts = new IllustratorSaveOptions();
src.saveAs(new File("TARGET"),opts);
src.close(SaveOptions.DONOTSAVECHANGES);
'''.replace('SOURCE',esc(svg)).replace('TARGET',esc(target))
        else:
            target=out/(stem+'.indd')
            # InDesign legacy ExtendScript; images remain linked/placed and text does not magically become editable.
            preview=board['preview']
            # PDF point dimensions avoid pixel rounding; legacy PDF manifests use render DPI.
            dpi=data.get('settings',{}).get('dpi',72) if board.get('source_type')=='pdf' else 72
            width=board.get('width_pt',board['width']*72/dpi)
            height=board.get('height_pt',board['height']*72/dpi)
            script='''#target indesign
if (File("TARGET").exists) throw new Error("Output exists; refusing overwrite");
var doc=app.documents.add();
try {
doc.documentPreferences.facingPages=false;
doc.documentPreferences.pageWidth="WIDTHpt";
doc.documentPreferences.pageHeight="HEIGHTpt";
doc.viewPreferences.horizontalMeasurementUnits=MeasurementUnits.POINTS;
doc.viewPreferences.verticalMeasurementUnits=MeasurementUnits.POINTS;
doc.viewPreferences.rulerOrigin=RulerOrigin.PAGE_ORIGIN;
doc.zeroPoint=[0,0];
var box=doc.pages.item(0).rectangles.add();
box.geometricBounds=["0pt","0pt","HEIGHTpt","WIDTHpt"]; box.strokeWeight=0;
box.place(File("SOURCE")); box.fit(FitOptions.PROPORTIONALLY);
box.fit(FitOptions.CENTER_CONTENT);
doc.save(File("TARGET"));
} finally {
doc.close(SaveOptions.NO);
}
'''.replace('WIDTH',str(width)).replace('HEIGHT',str(height)).replace('SOURCE',esc(preview)).replace('TARGET',esc(target))
        job=out/(stem+'_'+app+'.jsx');job.write_text(script,encoding='utf-8');jobs.append({'script':str(job),'expected_output':str(target),'status':'requires_adobe_execution'})
    return jobs
