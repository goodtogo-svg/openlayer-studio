"""Article/column stories for native PDF reconstruction, with layout ownership."""
import json
import statistics
from collections import Counter
from pathlib import Path
from PIL import ImageFont
from .runtime import atomic_json,digest


def inside(span, rect):
    x,y=span['origin'];return rect[0]-.3<=x<rect[2]+.3 and rect[1]<=y<=rect[3]


def lines_for(spans):
    rows=[]
    for s in sorted(spans,key=lambda x:(x['origin'][1],x['origin'][0])):
        row=next((r for r in reversed(rows) if abs(r['baseline']-s['origin'][1])<.6),None)
        if row is None:
            row={'baseline':s['origin'][1],'spans':[]};rows.append(row)
        row['spans'].append(s)
    for row in rows:
        row['spans'].sort(key=lambda s:s['origin'][0]);row['left']=min(s['bbox'][0] for s in row['spans']);row['right']=max(s['bbox'][2] for s in row['spans']);row['size']=max(s['size'] for s in row['spans'])
    return rows


def concatenate(left,right):
    if left and right and left[-1].isascii() and left[-1].isalnum() and right[0].isascii() and right[0].isalnum():return left+' '+right
    return left+right


def style_key(span):return (span['font'],round(span['size'],2),span['color'])


def paragraphize(columns,single=False):
    paragraphs=[];previous=None
    for ci,column in enumerate(columns):
        rows=lines_for(column['spans'])
        for row in rows:
            first=row['spans'][0];indent=max(0,row['left']-column['rect'][0]);rowtext=''.join(s['text'] for s in row['spans']).strip()
            if not rowtext:continue
            changed=previous and style_key(first)!=style_key(previous['spans'][0])
            indented=indent>first['size']*.7 and indent<first['size']*3
            new=not paragraphs or (not single and (changed or (indented and (ci==previous['column'] or row['baseline']!=previous['baseline']))))
            if new:
                paragraphs.append({'text':'','runs':[],'source_ids':[],'size':first['size'],'font':first['font'],'color':first['color'],'indent':indent if indented else 0,'baseline_samples':[],'column':ci})
            p=paragraphs[-1]
            if p['text']:
                # Only add a word separator for an actual Latin line continuation.
                joined=concatenate(p['text'],rowtext);separator=joined[len(p['text']):len(joined)-len(rowtext)]
                if separator:p['runs'].append({'text':separator,'font':first['font'],'size':first['size'],'color':first['color']})
                p['text']+=separator
            for s in row['spans']:
                p['text']+=s['text'];p['runs'].append({k:s[k] for k in ('text','font','size','color')});p['source_ids'].append(s['id'])
            if previous and previous['column']==ci and not new:
                delta=row['baseline']-previous['baseline']
                if delta>first['size']*.8 and delta<first['size']*2:p['baseline_samples'].append(delta)
            previous=dict(row,column=ci)
    all_leading=[v for p in paragraphs for v in p['baseline_samples']]
    for p in paragraphs:
        p['leading']=statistics.mean(p['baseline_samples']) if p['baseline_samples'] else statistics.median(all_leading) if all_leading else p['size']*1.4
    return paragraphs


def _font_scale(spans, font_map):
    ratios=[]
    for s in spans:
        path=font_map[s['font']].get('file')
        if path and len(s['text'])>4:
            f=ImageFont.truetype(path,round(s['size']*64));length=f.getlength(s['text'])/64
            if length:ratios.append((s['bbox'][2]-s['bbox'][0])/length*100)
    return max(88,min(110,statistics.median(ratios))) if ratios else 100


def _frame(column, shaped=False):
    rows=lines_for(column['spans']);first=rows[0];last=rows[-1];size=first['size'];left=column['rect'][0];right=column['rect'][2]
    # Use full intended column width; reserve the original baseline and bottom.
    top=first['baseline']-size*1.2;bottom=max(column['rect'][3],last['baseline']+last['size']*.7)
    frame={'bounds':[top,left,bottom,right],'baseline':first['baseline'],'offset':size*1.2}
    if shaped:
        # One shaped frame wraps around the photo; this is not one frame per line.
        points=[[right,top],[right,bottom],[left,bottom]]
        for i in range(len(rows)-1,-1,-1):
            edge=max(left,rows[i]['left']);y=top if i==0 else (rows[i-1]['baseline']+rows[i]['baseline'])/2-size*.4
            points.append([edge,y])
            if i>0:points.append([max(left,rows[i-1]['left']),y])
        frame['path']=points
    return frame


def build_article_plan(base_plan,layout,out):
    import pymupdf
    base=base_plan if isinstance(base_plan,dict) else json.loads(Path(base_plan).read_text());layout=json.loads(Path(layout).read_text());out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    if digest(base['source'])!=base['source_sha256']:raise ValueError('Source changed')
    if layout.get('schema')!='ali-article-layout/1':raise ValueError('Invalid article layout schema')
    if layout.get('source_sha256',base['source_sha256'])!=base['source_sha256']:raise ValueError('Layout belongs to a different source')
    font_map=base['font_map'];pages=[]
    document=pymupdf.open(base['source'])
    if len(layout['pages'])!=len(document):raise ValueError('Layout must describe every source page')
    for pi,page in enumerate(document):
        spec=layout['pages'][pi];selected=[];preserved=[]
        for bi,block in enumerate(page.get_text('dict')['blocks']):
            for li,line in enumerate(block.get('lines',[])):
                for si,s in enumerate(line['spans']):
                    item={k:s[k] for k in ('text','font','size','bbox','origin','color','alpha') if k in s};item.update(id=f'{pi}:{bi}:{li}:{si}',block=bi,line=li)
                    if s['font'] in font_map and list(line['dir'])==[1,0] and not any(inside(item,r) for r in spec.get('preserve_regions',[])):
                        selected.append(item)
                    else:preserved.append(item)
        for span in selected:
            owners=[a['id'] for a in spec.get('articles',[]) if any(inside(span,rect) for rect in a['columns'])]
            owners += [b['id'] for b in spec.get('bios',[]) if inside(span,b['rect'])]
            if len(owners)>1:raise ValueError('Text assigned to overlapping article regions: '+span['id'])
        groups=[];used=set()
        for article in spec.get('articles',[]):
            columns=[]
            for rect in article['columns']:
                members=[s for s in selected if s['id'] not in used and inside(s,rect)]
                if members:columns.append({'rect':rect,'spans':members});used.update(s['id'] for s in members)
            if columns:groups.append({'id':article['id'],'kind':'article','frames':[_frame(c) for c in columns],'paragraphs':paragraphize(columns),'scale':_font_scale([s for c in columns for s in c['spans']],font_map)})
        for bio in spec.get('bios',[]):
            members=[s for s in selected if s['id'] not in used and inside(s,bio['rect'])]
            if members:
                c={'rect':bio['rect'],'spans':members};groups.append({'id':bio['id'],'kind':'profile','frames':[_frame(c,shaped=True)],'paragraphs':paragraphize([c],single=True),'scale':_font_scale(members,font_map)});used.update(s['id'] for s in members)
        # Independent captions and table cells are grouped within their PDF block.
        blocks={}
        for s in selected:
            if s['id'] not in used:blocks.setdefault(s['block'],[]).append(s)
        for block_id,members in blocks.items():
            rows=lines_for(members);parallel=any(len(r['spans'])>1 and any(b['bbox'][0]-a['bbox'][2]>r['size']*.5 for a,b in zip(r['spans'],r['spans'][1:])) for r in rows)
            cells=[]
            if parallel:
                for s in members:
                    cell=next((c for c in cells if abs(c[0]['bbox'][0]-s['bbox'][0])<s['size']*.6),None)
                    if cell is None:cells.append([s])
                    else:cell.append(s)
            else:cells=[members]
            for ci,cell in enumerate(cells):
                rect=[min(s['bbox'][0] for s in cell),min(s['bbox'][1] for s in cell),max(s['bbox'][2] for s in cell)+.7,max(s['bbox'][3] for s in cell)+1]
                c={'rect':rect,'spans':cell};groups.append({'id':f'p{pi+1}-block-{block_id}-cell-{ci}','kind':'caption_or_cell','source_line_count':len(lines_for(cell)),'frames':[_frame(c)],'paragraphs':paragraphize([c],single=True),'scale':_font_scale(cell,font_map)})
        assigned=[sid for g in groups for p in g['paragraphs'] for sid in p['source_ids']]
        if Counter(assigned)!=Counter(s['id'] for s in selected):raise RuntimeError('Some source text is missing or duplicated')
        # Narrow glyph-band removal preserves nearby headings. Require exact
        # conservation of every unconverted source span before accepting output.
        before=Counter((s['text'],s['font']) for s in preserved)
        for s in selected:
            x0,_,x1,_=s['bbox'];y=s['origin'][1];page.add_redact_annot([x0+.01,y-s['size']*.4,x1-.01,y-s['size']*.25],fill=False,cross_out=False)
        if selected:page.apply_redactions(images=0,graphics=0,text=0)
        after=Counter((s['text'],s['font']) for b in page.get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans'])
        if before!=after:raise RuntimeError(f'Preserved source text changed on page {pi+1}; refine layout preservation')
        pages.append({'page':pi+1,'width_pt':page.rect.width,'height_pt':page.rect.height,'groups':groups,'source_spans':len(selected),'preserved_spans':len(preserved)})
    for page_plan in pages:
        for group in page_plan['groups']:
            body_size=statistics.median(p['size'] for p in group['paragraphs'])
            for p in group['paragraphs']:
                p['heading']=group['kind']=='article' and len(p['text'])<60 and (p['font'].endswith(('-Bold','-Black')) or p['size']>body_size*1.075)
    background=out/'background-original-vectors.pdf';document.save(background,garbage=4,deflate=True);document.close()
    plan={'schema':'ali-article-rebuild/1','source':base['source'],'source_sha256':base['source_sha256'],'background_pdf':str(background),'font_map':font_map,'pages':pages,'indd':str(out/'article-flow.indd'),'idml':str(out/'article-flow.idml'),'proof':str(out/'article-proof.pdf'),'out':str(out)}
    if digest(base['source'])!=base['source_sha256']:raise RuntimeError('Source changed during article preparation')
    atomic_json(out/'article-plan.json',plan);(out/'build-articles.jsx').write_text(article_script(plan));return plan


def article_script(plan):
    return '''#target indesign
var plan=PLAN;
if(File(plan.indd).exists||File(plan.idml).exists||File(plan.proof).exists)throw new Error("Output exists");
var oldPDF=app.pdfPlacePreferences.properties,oldRedraw=app.scriptPreferences.enableRedraw,d=null,metrics=[];
function progress(message){var f=File(plan.out+"/progress.txt");f.open('w');f.writeln(message);f.close();}
function color(value){var name='RGB_'+value,c=d.colors.itemByName(name);if(!c.isValid)c=d.colors.add({name:name,model:ColorModel.PROCESS,space:ColorSpace.RGB,colorValue:[value>>16&255,value>>8&255,value&255]});return c;}
try {
 app.scriptPreferences.enableRedraw=false;
 d=app.documents.add();d.documentPreferences.facingPages=false;d.documentPreferences.pagesPerDocument=plan.pages.length;d.documentPreferences.pageWidth=plan.pages[0].width_pt+'pt';d.documentPreferences.pageHeight=plan.pages[0].height_pt+'pt';
 d.viewPreferences.horizontalMeasurementUnits=MeasurementUnits.POINTS;d.viewPreferences.verticalMeasurementUnits=MeasurementUnits.POINTS;d.viewPreferences.rulerOrigin=RulerOrigin.PAGE_ORIGIN;d.zeroPoint=[0,0];
 var bg=d.layers[0];bg.name='Original graphics and preserved type';var layer=d.layers.add({name:'Article stories and threaded columns'});
 for(var pi=0;pi<plan.pages.length;pi++){
  var p=plan.pages[pi],page=d.pages[pi];var b=page.rectangles.add(bg);b.geometricBounds=[0,0,p.height_pt,p.width_pt];b.strokeWeight=0;app.pdfPlacePreferences.pageNumber=pi+1;app.pdfPlacePreferences.pdfCrop=PDFCrop.CROP_MEDIA;b.place(File(plan.background_pdf));b.fit(FitOptions.PROPORTIONALLY);b.fit(FitOptions.CENTER_CONTENT);
  for(var gi=0;gi<p.groups.length;gi++){
   var g=p.groups[gi];progress('page '+(pi+1)+' story '+gi+'/'+p.groups.length);var frames=[];
   for(var fi=0;fi<g.frames.length;fi++){
    var fs=g.frames[fi],t=page.textFrames.add(layer);t.geometricBounds=fs.bounds;if(fs.path)t.paths[0].entirePath=fs.path;
    t.textFramePreferences.insetSpacing=[0,0,0,0];t.textFramePreferences.ignoreWrap=true;t.textFramePreferences.firstBaselineOffset=FirstBaseline.FIXED_HEIGHT;t.textFramePreferences.minimumFirstBaselineOffset=fs.offset;t.label=g.id+'|'+fi;t.name=g.id+' column '+(fi+1);
    if(frames.length)frames[frames.length-1].nextTextFrame=t;frames.push(t);
   }
   var paragraphs=[];for(var k=0;k<g.paragraphs.length;k++)paragraphs.push(g.paragraphs[k].text);
   frames[0].contents=paragraphs.join('\\r');var story=frames[0].parentStory;var offset=0;
   for(var k=0;k<g.paragraphs.length;k++){
    var para=g.paragraphs[k],range=story.paragraphs[k];
    range.appliedFont=app.fonts.itemByName(plan.font_map[para.font].adobe_name);range.pointSize=para.size;range.leading=para.leading;range.horizontalScale=g.scale;range.tracking=0;range.noBreak=false;range.hyphenation=false;range.characterAlignment=CharacterAlignment.ALIGN_BASELINE;range.gridAlignment=GridAlignment.NONE;range.justification=Justification.LEFT_JUSTIFIED;range.firstLineIndent=para.indent;range.leftIndent=0;range.rightIndent=0;range.spaceBefore=0;range.spaceAfter=0;range.fillColor=color(para.color);range.keepWithNext=para.heading?2:0;range.keepAllLinesTogether=!!para.heading;
    var pos=offset;for(var ri=0;ri<para.runs.length;ri++){var run=para.runs[ri];if(run.text.length){var chars=story.characters.itemByRange(pos,pos+run.text.length-1);chars.appliedFont=app.fonts.itemByName(plan.font_map[run.font].adobe_name);chars.pointSize=run.size;chars.fillColor=color(run.color);pos+=run.text.length;}}
    offset+=para.text.length+1;
   }
   d.recompose();
   if(g.kind=='caption_or_cell' && g.source_line_count==1){
    var originalBounds=frames[0].geometricBounds;var expanded=originalBounds.slice(0);expanded[3]+=200;expanded[2]+=20;frames[0].geometricBounds=expanded;
    story.texts[0].justification=Justification.LEFT_ALIGN;story.texts[0].noBreak=true;d.recompose();
    if(frames[0].lines.length){var ln=frames[0].lines[0],width=ln.endHorizontalOffset-ln.horizontalOffset;story.texts[0].horizontalScale=g.scale*(originalBounds[3]-originalBounds[1]-.25)/width;}
    frames[0].geometricBounds=originalBounds;d.recompose();
   }
   // Correct the first baseline without fixing every original line position.
   for(var fi=0;fi<frames.length;fi++){if(frames[fi].lines.length){var dy=g.frames[fi].baseline-frames[fi].lines[0].baseline;frames[fi].move(undefined,[0,dy]);}}
   d.recompose();var attempts=0;
   while(story.overflows && attempts<8){for(var k=0;k<story.paragraphs.length;k++)story.paragraphs[k].leading=g.paragraphs[Math.min(k,g.paragraphs.length-1)].leading*(1-.01*(attempts+1));d.recompose();attempts++;}
   if(story.overflows && g.kind=='caption_or_cell' && g.source_line_count>1){var gb=frames[0].geometricBounds;gb[2]+=g.paragraphs[0].leading+1;frames[0].geometricBounds=gb;d.recompose();}
   metrics.push(g.id+'\\t'+g.kind+'\\t'+frames.length+'\\t'+story.paragraphs.length+'\\t'+story.overflows+'\\t'+attempts);
  }
 }
 bg.locked=true;progress('saving INDD');d.save(File(plan.indd));d.exportFile(ExportFormat.INDESIGN_MARKUP,File(plan.idml));progress('exporting PDF');d.exportFile(ExportFormat.PDF_TYPE,File(plan.proof),false);
 var f=File(plan.out+'/story-metrics.tsv');f.open('w');f.writeln('id\\tkind\\tframes\\tparagraphs\\toverset\\tfit_steps');for(var i=0;i<metrics.length;i++)f.writeln(metrics[i]);f.close();progress('complete');
} finally {try{if(d&&d.isValid)d.close(SaveOptions.NO);}finally{app.pdfPlacePreferences.properties=oldPDF;app.scriptPreferences.enableRedraw=oldRedraw;}}
'''.replace('PLAN',json.dumps(plan,ensure_ascii=True),1)
