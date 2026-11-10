"""Conservative PDF to native InDesign text draft, with original vector fallback.

Only explicit font mappings become live type in this review-required draft. Unsupported/effected text stays in
an original vector PDF background. No raster inpainting or invented background.
"""
import json
import math
from pathlib import Path
from .runtime import atomic_json, digest


def classify_spans(spans, font_map):
    editable, preserved = [], []
    for i, span in enumerate(spans):
        reason = None
        if span['font'] not in font_map:
            reason = 'unmapped_font'
        elif span.get('direction', [1, 0]) != [1, 0]:
            reason = 'rotated_text'
        elif span.get('alpha', 255) != 255:
            reason = 'translucent_text'
        elif not span['text'].strip() or '\ufffd' in span['text']:
            reason = 'empty_or_unreliable_unicode'
        elif any(j != i and other['text'] == span['text'] and
                 max(abs(a-b) for a,b in zip(span['bbox'], other['bbox'])) < 3
                 for j,other in enumerate(spans)):
            reason = 'overlapping_text_effect'
        if reason:
            preserved.append(dict(span, preserve_reason=reason))
        else:
            editable.append(dict(span, font_mapping=font_map[span['font']]))
    # Rectangular PDF redaction also touches overlapping glyphs. Keep every
    # ambiguous overlap intact rather than erasing an unconverted text effect.
    changed = True
    while changed:
        changed = False
        for span in editable[:]:
            a = span['bbox']
            if any(min(a[2],p['bbox'][2]) > max(a[0],p['bbox'][0]) and
                   min(a[3],p['bbox'][3]) > max(a[1],p['bbox'][1])
                   for p in preserved):
                editable.remove(span)
                preserved.append(dict(span, preserve_reason='overlaps_preserved_text'))
                changed = True
    return editable, preserved


def prepare_rebuild(source, out, font_map):
    import pymupdf
    source = Path(source).expanduser().resolve()
    out = Path(out).resolve()
    if out == source or out in source.parents:
        raise ValueError('Output overlaps source')
    out.mkdir(parents=True, exist_ok=False)
    original_hash = digest(source)
    background = out/'background-original-vectors.pdf'
    pages = []
    with pymupdf.open(source) as document:
        if not document.is_pdf or document.needs_pass:raise ValueError('An unlocked PDF is required')
        for n,page in enumerate(document):
            if page.rotation:raise ValueError('Rotated PDF pages require coordinate normalization before rebuilding')
            if n and (abs(page.rect.width-pages[0]['width_pt'])>.01 or abs(page.rect.height-pages[0]['height_pt'])>.01):raise ValueError('Mixed page dimensions require a per-page resize adapter')
            spans = []
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines', []):
                    for span in line['spans']:
                        item = {key:span[key] for key in ('text','font','size','bbox','origin','color','alpha') if key in span}
                        item['direction'] = list(line['dir'])
                        spans.append(item)
            editable, preserved = classify_spans(spans, font_map)
            for span in editable:
                page.add_redact_annot(span['bbox'], fill=False, cross_out=False)
            if editable:
                page.apply_redactions(images=0, graphics=0, text=0)
            pages.append({'page':n+1, 'width_pt':page.rect.width, 'height_pt':page.rect.height,
                          'editable_count':len(editable), 'preserved_count':len(preserved),
                          'editable':editable, 'preserved':preserved})
        document.save(background, garbage=4, deflate=True)
    plan = {'schema':'ali-pdf-rebuild/1', 'source':str(source), 'source_sha256':original_hash,
            'background_pdf':str(background), 'pages':pages, 'font_map':font_map,
            'expected_indd':str(out/'editable-draft.indd'),
            'expected_proof':str(out/'rebuild-proof.pdf'),
            'expected_idml':str(out/'editable-draft.idml'),
            'status':'requires_adobe_execution',
            'limitations':['Mapped fonts require visual review; mapping is not exact font identity proof',
                           'Each PDF span becomes an independently editable text frame, not a reflowing article',
                           'Special/effected text and graphics remain linked original PDF vectors']}
    script = out/'rebuild-indesign.jsx'
    plan['script'] = str(script)
    atomic_json(out/'rebuild-plan.json',plan)
    script.write_text(_script(plan), encoding='utf-8')
    (out/'verify-reopen.jsx').write_text(verification_script(plan), encoding='utf-8')
    if digest(source) != original_hash:
        raise RuntimeError('Source changed during rebuild preparation')
    return plan


def _script(plan):
    # JSON is embedded as a JavaScript literal: no reliance on ExtendScript JSON.
    data = json.dumps(plan, ensure_ascii=True)
    return '''#target indesign
var plan=PLAN;
if(File(plan.expected_indd).exists || File(plan.expected_proof).exists || File(plan.expected_idml).exists) throw new Error("Refusing overwrite");
var previousPDF=app.pdfPlacePreferences.properties;
var d=null;
var verification=[];
var previousRedraw=app.scriptPreferences.enableRedraw;
function progress(message){var f=File(File(plan.script).parent.fsName+"/build-progress.txt");f.encoding="UTF-8";f.open("w");f.writeln(message);f.close();}
try {
 // Fail before building any document if a mapped installed font is missing.
 for(var name in plan.font_map){var font=app.fonts.itemByName(plan.font_map[name].adobe_name);if(!font.isValid || font.status!=FontStatus.INSTALLED)throw new Error("Missing font: "+name);}
 app.scriptPreferences.enableRedraw=false;
 progress("starting document");
 d=app.documents.add();
 d.documentPreferences.facingPages=false;
 d.documentPreferences.pagesPerDocument=plan.pages.length;
 d.documentPreferences.pageWidth=plan.pages[0].width_pt+"pt";
 d.documentPreferences.pageHeight=plan.pages[0].height_pt+"pt";
 d.viewPreferences.horizontalMeasurementUnits=MeasurementUnits.POINTS;
 d.viewPreferences.verticalMeasurementUnits=MeasurementUnits.POINTS;
 d.viewPreferences.rulerOrigin=RulerOrigin.PAGE_ORIGIN;d.zeroPoint=[0,0];
 var backgroundLayer=d.layers.item(0);backgroundLayer.name="Original vectors and preserved type";
 var textLayer=d.layers.add({name:"Editable text - font mapping requires review"});
 for(var p=0;p<plan.pages.length;p++){
  var spec=plan.pages[p],page=d.pages[p];
  var frame=page.rectangles.add(backgroundLayer);
  frame.geometricBounds=[0,0,spec.height_pt,spec.width_pt];frame.strokeWeight=0;
  app.pdfPlacePreferences.pageNumber=p+1;
  app.pdfPlacePreferences.pdfCrop=PDFCrop.CROP_MEDIA;
  frame.place(File(plan.background_pdf));frame.fit(FitOptions.PROPORTIONALLY);frame.fit(FitOptions.CENTER_CONTENT);
  var overflow=0,lowScale=0,maxBaselineError=0;
  for(var i=0;i<spec.editable.length;i++){
   if(i%25===0)progress("page "+(p+1)+" frame "+i+" / "+spec.editable.length);
   var s=spec.editable[i];var t=page.textFrames.add(textLayer);
   var baseline=s.origin[1],left=s.origin[0],size=s.size;
   var targetWidth=s.bbox[2]-s.bbox[0];
   t.geometricBounds=[baseline-size*1.5,left,baseline+size*1.5,left+Math.max(targetWidth*3,size*5)];
   t.textFramePreferences.insetSpacing=[0,0,0,0];
   t.textFramePreferences.ignoreWrap=true;
   t.textFramePreferences.firstBaselineOffset=FirstBaseline.FIXED_HEIGHT;
   t.textFramePreferences.minimumFirstBaselineOffset=size*1.5;
   t.contents=s.text;t.label="PDF span "+p+":"+i+" | "+s.font;
   var txt=t.parentStory.texts.item(0);
   txt.appliedFont=app.fonts.itemByName(s.font_mapping.adobe_name);
   txt.pointSize=size;txt.tracking=0;txt.horizontalScale=100;txt.noBreak=true;
   txt.justification=Justification.LEFT_ALIGN;txt.characterAlignment=CharacterAlignment.ALIGN_BASELINE;txt.gridAlignment=GridAlignment.NONE;
   var colorName="PDFRGB_"+s.color;
   var color=d.colors.itemByName(colorName);
   if(!color.isValid)color=d.colors.add({name:colorName,model:ColorModel.PROCESS,space:ColorSpace.RGB,colorValue:[(s.color>>16)&255,(s.color>>8)&255,s.color&255]});
   txt.fillColor=color;
   d.recompose();
   if(t.lines.length){var line=t.lines[0];var actual=line.endHorizontalOffset-line.horizontalOffset;var scale=actual>0?targetWidth/actual*100:100;
    if(scale<70 || scale>140)lowScale++;
    txt.horizontalScale=Math.max(50,Math.min(200,scale));}
   d.recompose();
   if(t.lines.length){var deltaX=left-t.lines[0].horizontalOffset;var deltaY=baseline-t.lines[0].baseline;t.move(undefined,[deltaX,deltaY]);}
   d.recompose();
   if(t.lines.length)maxBaselineError=Math.max(maxBaselineError,Math.abs(baseline-t.lines[0].baseline));
   if(t.overflows)overflow++;
  }
  verification.push([p+1,spec.editable.length,spec.preserved.length,overflow,lowScale,maxBaselineError].join("\\t"));
 }
 backgroundLayer.locked=true;
 progress("saving native INDD");
 d.save(File(plan.expected_indd));
 d.exportFile(ExportFormat.INDESIGN_MARKUP,File(plan.expected_idml));
 progress("exporting proof PDF");
 d.exportFile(ExportFormat.PDF_TYPE,File(plan.expected_proof),false);
 var f=File(File(plan.script).parent.fsName+"/build-metrics.tsv");f.encoding="UTF-8";f.open("w");
 f.writeln("page\\teditable_frames\\tpreserved_spans\\toverflow\\textreme_scale\\tmax_baseline_error_pt");for(var j=0;j<verification.length;j++)f.writeln(verification[j]);f.close();
} finally {
 try {if(d && d.isValid)d.close(SaveOptions.NO);}
 finally {app.scriptPreferences.enableRedraw=previousRedraw;app.pdfPlacePreferences.properties=previousPDF;}
}
'''.replace('PLAN', data, 1)


def verification_script(plan):
    return '''#target indesign
var plan=PLAN;
var d=app.open(File(plan.expected_indd),false);
try {
 var badFonts=0,badLinks=0,overset=0,maxBaselineError=0;
 var fonts=d.fonts.everyItem().status;
 for(var i=0;i<fonts.length;i++)if(fonts[i]!=FontStatus.INSTALLED)badFonts++;
 var links=d.links.everyItem().status;
 for(var i=0;i<links.length;i++)if(links[i]!=LinkStatus.NORMAL)badLinks++;
 for(var i=0;i<d.textFrames.length;i++){
  var t=d.textFrames[i];if(t.overflows)overset++;
  var match=t.label.match(/PDF span (\\d+):(\\d+) /);
  if(match && t.lines.length){var s=plan.pages[Number(match[1])].editable[Number(match[2])];maxBaselineError=Math.max(maxBaselineError,Math.abs(t.lines[0].baseline-s.origin[1]));}
 }
 var output='{"saved_and_reopened":true,"pages":'+d.pages.length+',"text_frames":'+d.textFrames.length+',"links":'+d.links.length+',"width_pt":'+d.documentPreferences.pageWidth+',"height_pt":'+d.documentPreferences.pageHeight+',"overset":'+overset+',"bad_fonts":'+badFonts+',"bad_links":'+badLinks+',"max_baseline_error_pt":'+maxBaselineError+'}';
 var f=File(File(plan.script).parent.fsName+"/reopened-verification.json");f.encoding="UTF-8";f.open("w");f.write(output);f.close();
} finally {d.close(SaveOptions.NO);}
'''.replace('PLAN', json.dumps(plan,ensure_ascii=True),1)
