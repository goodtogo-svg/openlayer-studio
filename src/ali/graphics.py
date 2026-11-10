"""PDF graphic objects: preserve source paint, clips and original image samples."""
from pathlib import Path
import copy
import io
import json
import math
import struct
from collections import Counter
import pymupdf as fitz
from .runtime import atomic_json,digest

PAINT={'S','s','f','F','f*','B','B*','b','b*'}
PATH={'m','l','c','v','y','h','re'}
SETTINGS={'w':'w','J':'J','j':'j','M':'M','d':'d','ri':'ri','i':'i',
          'g':'fill','rg':'fill','k':'fill','sc':'fill','scn':'fill','cs':'fillspace',
          'G':'stroke','RG':'stroke','K':'stroke','SC':'stroke','SCN':'stroke','CS':'strokespace'}


def _point(x,y,m):
    p=fitz.Point(float(x),float(y))*m;return [p.x,p.y]


def global_path(ops,m):
    result=[];current=None;start=None
    for args,op in ops:
        if op=='m':
            current=_point(*args,m);start=current;result.append((current,'m'))
        elif op=='l':current=_point(*args,m);result.append((current,'l'))
        elif op=='c':
            vals=sum([_point(args[i],args[i+1],m) for i in (0,2,4)],[]);current=vals[-2:];result.append((vals,'c'))
        elif op=='v':
            vals=list(current)+_point(args[0],args[1],m)+_point(args[2],args[3],m);current=vals[-2:];result.append((vals,'c'))
        elif op=='y':
            end=_point(args[2],args[3],m);vals=_point(args[0],args[1],m)+end+end;current=end;result.append((vals,'c'))
        elif op=='re':
            x,y,w,h=map(float,args);points=[_point(x,y,m),_point(x+w,y,m),_point(x+w,y+h,m),_point(x,y+h,m)]
            result += [(points[0],'m')]+[(p,'l') for p in points[1:]]+[([],'h')];current=start=points[0]
        elif op=='h':result.append(([],'h'));current=start
    return result


def path_rect(ops):
    points=[(v[i],v[i+1]) for v,op in ops for i in range(0,len(v),2)]
    if not points:return fitz.Rect()
    return fitz.Rect(min(x for x,y in points),min(y for x,y in points),max(x for x,y in points),max(y for x,y in points))


def native_path(ops,page_matrix):
    """Convert a single closed cubic clip into InDesign Bezier anchors."""
    anchors=[];closed=False
    for args,op in ops:
        if op=='m':
            if anchors:return None
            p=_point(*args,page_matrix);anchors.append([p[:],p[:],p[:]])
        elif op=='l':p=_point(*args,page_matrix);anchors.append([p[:],p[:],p[:]])
        elif op=='c':
            p1=_point(args[0],args[1],page_matrix);p2=_point(args[2],args[3],page_matrix);end=_point(args[4],args[5],page_matrix)
            anchors[-1][2]=p1;anchors.append([p2,end,end[:]])
        elif op=='h':closed=True
    if len(anchors)>1 and math.dist(anchors[-1][1],anchors[0][1])<.001:
        anchors[0][0]=anchors[-1][0];anchors.pop()
    # PDF clipping implicitly closes open subpaths, even without an h token.
    return anchors if len(anchors)>=3 else None


def select_clip(candidates):
    if not candidates:return None
    smallest=min(c['box'].get_area() for c in candidates)
    # Equivalent bounding boxes differ by floating point noise. Prefer the
    # curved shape over a redundant image rectangle within that tolerance.
    equivalent=[c for c in candidates if c['box'].get_area()<=smallest*1.0001]
    return max(equivalent,key=lambda c:sum(op=='c' for _,op in c['path']))


def scan_page(pdf,page_index):
    import pikepdf
    page=pdf.pages[page_index]
    media=[float(v) for v in page.mediabox];page_rect=fitz.Rect(media)
    transform=fitz.Matrix(1,0,0,-1,-media[0],media[3])
    state={'matrix':fitz.Matrix(1,0,0,1,0,0),'clips':[],'gs':[],'settings':{}}
    stack=[];path=[];path_matrix=None;pending=None;records=[]
    instructions=pikepdf.parse_content_stream(page)
    for index,instruction in enumerate(instructions):
        args=list(instruction.operands);op=str(instruction.operator)
        if op=='q':stack.append({'matrix':state['matrix'],'clips':state['clips'][:],'gs':state['gs'][:],'settings':state['settings'].copy()})
        elif op=='Q':state=stack.pop()
        elif op=='cm':state['matrix']=fitz.Matrix(*map(float,args))*state['matrix']
        elif op=='gs':state['gs'].append((args[0],state['matrix']))
        elif op in SETTINGS:state['settings'][SETTINGS[op]]=(args,op)
        elif op in PATH:
            if not path:path_matrix=state['matrix']
            if tuple(state['matrix'])!=tuple(path_matrix):raise ValueError('A path changes transforms mid-construction; preserve it without splitting')
            path.append(([float(a) for a in args],op))
        elif op in ('W','W*'):pending=op
        elif op in PAINT or op=='n':
            gp=global_path(path,path_matrix or state['matrix'])
            if op in PAINT:
                box=path_rect(gp)
                if 'S' in op or op in ('s','B','B*','b','b*'):box+=(-.5,-.5,.5,.5)
                records.append({'index':index,'kind':'path','operator':op,'path':path[:],'matrix':state['matrix'],'clips':state['clips'][:],'gs':state['gs'][:],'settings':state['settings'].copy(),'box_pdf':box})
            if pending:state['clips'].append({'path':gp,'rule':pending,'box':path_rect(gp)});pending=None
            path=[];path_matrix=None
        elif op=='Do':
            obj=page.obj.Resources.XObject[args[0]]
            if str(obj.get('/Subtype'))=='/Image':
                box=fitz.Rect(0,0,1,1)*state['matrix'];clip=box & page_rect
                for c in state['clips']:clip &= c['box']
                if clip.width>1 and clip.height>1:
                    cs=obj.get('/ColorSpace');components=1
                    if isinstance(cs,pikepdf.Array) and str(cs[0])=='/ICCBased':components=int(cs[1].get('/N',3))
                    elif str(cs) in ('/DeviceRGB','/DeviceCMYK'):components={'/DeviceRGB':3,'/DeviceCMYK':4}[str(cs)]
                    records.append({'index':index,'kind':'image','name':str(args[0]),'object':obj,'matrix':state['matrix'],'clips':state['clips'][:],'gs':state['gs'][:],'settings':state['settings'].copy(),'box_pdf':box,'visible_pdf':clip,'box':list(clip*transform),'full_box':list(box*transform),'components':components,'pixels':[int(obj.Width),int(obj.Height)],'page_matrix':transform})
    for record in records:
        if record['kind']=='path':
            rect=record['box_pdf'] & page_rect
            for c in record['clips']:rect &= c['box']
            record['box']=list(rect*transform)
    return instructions,records,transform


def _op(args,name):
    import pikepdf
    return pikepdf.ContentStreamInstruction(args,pikepdf.Operator(name))


def paint_stream(record):
    """Rebuild the original graphics-state context around one paint operation."""
    import pikepdf
    result=[_op([],'q')]
    for clip in record['clips']:
        result += [_op(v,op) for v,op in clip['path']]+[_op([],clip['rule']),_op([],'n')]
    # Soft masks use the CTM in effect at gs application, not the final image CTM.
    current=fitz.Matrix(1,0,0,1,0,0)
    for name,matrix in record['gs']:
        result.append(_op(list(matrix*~current),'cm'));result.append(_op([name],'gs'));current=matrix
    result.append(_op(list(record['matrix']*~current),'cm'))
    for args,name in record['settings'].values():result.append(_op(args,name))
    if record['kind']=='image':result.append(_op([pikepdf.Name(record['name'])],'Do'))
    else:result += [_op(v,op) for v,op in record['path']]+[_op([],record['operator'])]
    result.append(_op([],'Q'));return result


def write_overlay(source,page_index,records,rect,path):
    import pikepdf
    output=pikepdf.Pdf.new();page=output.add_blank_page(page_size=(100,100))
    box=fitz.Rect(rect);page.obj.MediaBox=pikepdf.Array(list(box));page.obj.CropBox=pikepdf.Array(list(box))
    resources=source.make_indirect(source.pages[page_index].obj.Resources)
    page.obj.Resources=output.copy_foreign(resources)
    page.obj.Contents=output.make_stream(pikepdf.unparse_content_stream([op for r in records for op in paint_stream(r)]))
    page.remove_unreferenced_resources();output.save(path);output.close()


def _jpeg_icc(data,icc):
    from PIL import Image
    if not data.startswith(b'\xff\xd8') or not icc:return data
    with Image.open(io.BytesIO(data)) as im:
        if im.info.get('icc_profile'):return data
    chunks=[icc[i:i+65519] for i in range(0,len(icc),65519)];markers=[]
    for i,chunk in enumerate(chunks,1):
        payload=b'ICC_PROFILE\0'+bytes([i,len(chunks)])+chunk;markers.append(b'\xff\xe2'+struct.pack('>H',len(payload)+2)+payload)
    return data[:2]+b''.join(markers)+data[2:]


def extract_image(obj,prefix):
    import pikepdf
    image=pikepdf.PdfImage(obj);path=Path(image.extract_to(fileprefix=str(prefix)))
    cs=obj.get('/ColorSpace')
    if path.suffix.lower() in ('.jpg','.jpeg') and isinstance(cs,pikepdf.Array) and str(cs[0])=='/ICCBased':path.write_bytes(_jpeg_icc(path.read_bytes(),cs[1].read_bytes()))
    return path


def _rgba_image(obj,prefix):
    import pikepdf
    image=pikepdf.PdfImage(obj).as_pil_image().convert('RGB')
    if '/SMask' in obj:
        alpha=pikepdf.PdfImage(obj.SMask).as_pil_image().convert('L')
        if alpha.size!=image.size:raise ValueError('Image and soft-mask samples have different dimensions')
        image.putalpha(alpha)
    path=Path(str(prefix)+'.png');kwargs={};cs=obj.get('/ColorSpace')
    if isinstance(cs,pikepdf.Array) and str(cs[0])=='/ICCBased':kwargs['icc_profile']=cs[1].read_bytes()
    image.save(path,**kwargs);return path


def prepare_graphics(source_indd,source_background,out,classification=None):
    """Separate visible direct image paints and their nearby frame decorations.

    Text is never reconstructed here; the approved INDD is copied, and its story
    objects remain untouched. Complex non-image graphics stay in the base PDF.
    """
    import pikepdf
    source_indd=Path(source_indd).resolve();source_background=Path(source_background).resolve();out=Path(out).resolve()
    if not source_indd.is_file() or not source_background.is_file():raise FileNotFoundError('Approved INDD/background is missing')
    out.mkdir(parents=True,exist_ok=False);assets=out/'Links';assets.mkdir()
    classification=classification or {'illustration_regions':{}}
    plan={'schema':'ali-graphic-frames/1','approved_indd':str(source_indd),'approved_indd_sha256':digest(source_indd),'source_background':str(source_background),'source_background_sha256':digest(source_background),'out':str(out),'background':str(assets/'background-without-placed-images.pdf'),'indd':str(out/'graphic-frames.indd'),'idml':str(out/'graphic-frames.idml'),'proof':str(out/'graphic-frames-proof.pdf'),'pages':[]}
    pdf=pikepdf.Pdf.open(source_background)
    for pi in range(len(pdf.pages)):
        instructions,records,matrix=scan_page(pdf,pi);images=[r for r in records if r['kind']=='image'];paths=[r for r in records if r['kind']=='path'];taken=set();objects=[]
        for ii,image in enumerate(images):
            box=fitz.Rect(image['box']);full=fitz.Rect(image['full_box']);category='photo'
            for region in classification.get('illustration_regions',{}).get(str(pi+1),[]):
                if fitz.Rect(region).contains(box):category='illustration'
            id=f'p{pi+1}-{category}-{ii+1:03d}';prefix=assets/id
            obj=image['object'];cs=obj.get('/ColorSpace');axis=abs(image['matrix'].b)<1e-5 and abs(image['matrix'].c)<1e-5
            if '/SMask' in obj:asset=_rgba_image(obj,prefix)
            else:asset=extract_image(obj,prefix)
            # Pick the actual rounded/circular clip, not only its rectangular bbox.
            candidates=[c for c in image['clips'] if c['box'].width*c['box'].height<image['visible_pdf'].get_area()*1.15 and c['box'].width>1 and c['box'].height>1]
            clip=select_clip(candidates)
            shape=native_path(clip['path'],matrix) if clip else None
            if not axis:raise ValueError(f'{id}: rotated/skewed original image needs an affine graphic adapter')
            # Keep shadows/borders with their picture, retaining pre/post z-order.
            decorations=[]
            for record in paths:
                if record['index'] in taken:continue
                rect=fitz.Rect(record['box']);area=rect.get_area()
                if rect.width>12 and rect.height>12 and area>=box.get_area()*.75 and area<=box.get_area()*1.9:
                    expanded=rect+(-1,-1,1,1)
                    if expanded.contains(box) and math.dist(rect.tl+rect.br,box.tl+box.br)<16:
                        decorations.append(record);taken.add(record['index'])
            before=[r for r in decorations if r['index']<image['index']];after=[r for r in decorations if r['index']>image['index']]
            item={'id':id,'kind':category,'asset':str(asset),'image_resource':image['name'],'source_paint_index':image['index'],'frame_box':list(box),'image_box':list(full),'clip_path':shape,'original_pixels':image['pixels'],'effective_ppi':[image['pixels'][0]/full.width*72,image['pixels'][1]/full.height*72],'alpha_from_original_mask':'/SMask' in obj,'decorations':[]}
            for where,group in [('before',before),('after',after)]:
                if not group:continue
                bounds=fitz.Rect()
                for r in group:bounds|=r['box_pdf']
                path=assets/(id+'-'+where+'.pdf');write_overlay(pdf,pi,group,bounds,path)
                item['decorations'].append({'order':where,'asset':str(path),'box':list(bounds*matrix),'paint_count':len(group)})
            # A paint-exact PDF is retained as a test oracle, not as the native image.
            oracle=assets/(id+'-oracle.pdf');write_overlay(pdf,pi,[image],image['visible_pdf'],oracle);item['oracle']=str(oracle)
            objects.append(item);taken.add(image['index'])
        filtered=[]
        for index,op in enumerate(instructions):
            if str(op.operator)=='Do' and str(pdf.pages[pi].Resources.XObject[op.operands[0]].get('/Subtype'))=='/Image':continue
            if index not in taken:filtered.append(op)
            elif str(op.operator) in PAINT:filtered.append(_op([],'n'))
        pdf.pages[pi].obj.Contents=pdf.make_stream(pikepdf.unparse_content_stream(filtered));pdf.pages[pi].remove_unreferenced_resources()
        plan['pages'].append({'page':pi+1,'objects':objects,'removed_paints':len(taken),'remaining_background_is_vector_pdf':True})
    pdf.save(plan['background']);pdf.close()
    # No direct image call representing any extracted picture may remain below it.
    with pikepdf.Pdf.open(plan['background']) as check:
        for pi in range(len(check.pages)):
            if any(r['kind']=='image' for r in scan_page(check,pi)[1]):raise RuntimeError('A separated image is still painted in the background')
    if digest(source_indd)!=plan['approved_indd_sha256'] or digest(source_background)!=plan['source_background_sha256']:raise RuntimeError('Approved inputs changed')
    atomic_json(out/'graphics-plan.json',plan);(out/'build-graphics.jsx').write_text(graphics_script(plan));return plan


def graphics_script(plan):
    return '''#target indesign
var plan=PLAN;
if(File(plan.indd).exists||File(plan.idml).exists||File(plan.proof).exists)throw new Error('Refusing overwrite');
var d=null,oldPDF=app.pdfPlacePreferences.properties,oldRedraw=app.scriptPreferences.enableRedraw;
var working=File(plan.out+'/working-copy.indd');
for(var di=0;di<app.documents.length;di++){try{if(app.documents[di].fullName.fsName==File(plan.approved_indd).fsName){app.documents[di].saveACopy(working);break;}}catch(ignore){}}
d=app.open(working.exists?working:File(plan.approved_indd),false);
function progress(s){var f=File(plan.out+'/progress.txt');f.open('w');f.writeln(s);f.close();}
function rectangle(page,layer,box,name){var t=page.rectangles.add(layer);t.geometricBounds=[box[1],box[0],box[3],box[2]];t.strokeWeight=0;t.name=name;t.textWrapPreferences.textWrapMode=TextWrapModes.NONE;return t;}
function decoration(page,layer,item){var f=rectangle(page,layer,item.box,'Original border / shadow');app.pdfPlacePreferences.pageNumber=1;app.pdfPlacePreferences.pdfCrop=PDFCrop.CROP_MEDIA;f.place(File(item.asset));f.fit(FitOptions.PROPORTIONALLY);f.fit(FitOptions.CENTER_CONTENT);return f;}
try {
 app.scriptPreferences.enableRedraw=false;
 d.viewPreferences.horizontalMeasurementUnits=MeasurementUnits.POINTS;d.viewPreferences.verticalMeasurementUnits=MeasurementUnits.POINTS;d.viewPreferences.rulerOrigin=RulerOrigin.PAGE_ORIGIN;d.zeroPoint=[0,0];
 var base=d.layers.itemByName('Original graphics and preserved type').getElements()[0];base.locked=false;base.name='01 Base graphics and preserved type';
 var text=d.layers.itemByName('Article stories and threaded columns').getElements()[0];text.name='04 Article text';
 var photos=d.layers.add({name:'02 Photographs'});photos.move(LocationOptions.AFTER,text);
 var artwork=d.layers.add({name:'03 Illustrations and artwork'});artwork.move(LocationOptions.AFTER,text);
 // Delete only the old linked background rectangles from the copied document.
 for(var i=base.pageItems.length-1;i>=0;i--)base.pageItems[i].remove();
 for(var pi=0;pi<plan.pages.length;pi++){
  var page=d.pages[pi],p=plan.pages[pi];progress('page '+(pi+1)+' placing graphics');
  var bg=rectangle(page,base,[0,0,d.documentPreferences.pageWidth,d.documentPreferences.pageHeight],'Base without duplicate photos');app.pdfPlacePreferences.pageNumber=pi+1;app.pdfPlacePreferences.pdfCrop=PDFCrop.CROP_MEDIA;bg.place(File(plan.background));bg.fit(FitOptions.PROPORTIONALLY);bg.fit(FitOptions.CENTER_CONTENT);
  for(var i=0;i<p.objects.length;i++){
   var o=p.objects[i],layer=o.kind=='photo'?photos:artwork,items=[];
   for(var j=0;j<o.decorations.length;j++)if(o.decorations[j].order=='before')items.push(decoration(page,layer,o.decorations[j]));
   var frame=rectangle(page,layer,o.frame_box,o.id+' original image');frame.label=o.id;
   if(o.clip_path)frame.paths[0].entirePath=o.clip_path;
   frame.place(File(o.asset));frame.images[0].geometricBounds=[o.image_box[1],o.image_box[0],o.image_box[3],o.image_box[2]];items.push(frame);
   for(var j=0;j<o.decorations.length;j++)if(o.decorations[j].order=='after')items.push(decoration(page,layer,o.decorations[j]));
   if(items.length>1){var group=page.groups.add(items);group.name=o.id+' picture and frame';group.label=o.id+'|group';group.itemLayer=layer;}
  }
 }
 base.locked=true;d.recompose();progress('saving native INDD');d.save(File(plan.indd));d.exportFile(ExportFormat.INDESIGN_MARKUP,File(plan.idml));progress('exporting proof');d.exportFile(ExportFormat.PDF_TYPE,File(plan.proof),false);progress('complete');
}finally{try{d.close(SaveOptions.NO);}finally{app.pdfPlacePreferences.properties=oldPDF;app.scriptPreferences.enableRedraw=oldRedraw;}}
'''.replace('PLAN',json.dumps(plan,ensure_ascii=True),1)
