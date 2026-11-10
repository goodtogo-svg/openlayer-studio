"""Conservative local-first preflight & asset extraction. No hallucinated layers."""
from pathlib import Path
from PIL import Image
import json, hashlib, re, shutil, sys, time
from .language import SUPPORTED_LOCALES, audit_text

SUPPORTED={'.jpg','.jpeg','.png','.pdf','.psd'}
MAX_PIXELS=90_000_000
Image.MAX_IMAGE_PIXELS=MAX_PIXELS

def slug(s):
    return re.sub(r'[^a-zA-Z0-9_-]+','_',str(s))[:60].strip('_') or 'layer'

def checksum(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()

def process(source, out, *, dpi=144, vectorize=True, max_pages=10, upscale=1, locale="auto"):
    source=Path(source).expanduser().resolve();out=Path(out).expanduser().resolve()
    if source.suffix.lower() not in SUPPORTED or not source.is_file():
        raise ValueError('Unsupported or missing input (JPG/PNG/PDF/PSD only)')
    if not 1<=max_pages<=100:raise ValueError('max_pages must be 1..100')
    if dpi<72 or dpi>300:raise ValueError('dpi must be 72..300')
    if upscale not in (1,2,4):raise ValueError('upscale must be 1,2,4')
    if locale not in SUPPORTED_LOCALES:raise ValueError('unsupported locale')
    if out==source or out in source.parents:raise ValueError('output path overlaps source')
    out.mkdir(parents=True,exist_ok=True)
    assets=out/'assets';assets.mkdir(exist_ok=True)
    manifest={'schema':'ali/1','input':str(source),'sha256':checksum(source),'created_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'settings':{'dpi':dpi,'vectorize':vectorize,'max_pages':max_pages,'upscale':upscale,'locale':locale},'artboards':[],'warnings':[],'status':'preflight_complete'}
    ext=source.suffix.lower()
    if ext=='.pdf':
        import pymupdf
        doc=pymupdf.open(source)
        try:
            if doc.needs_pass:raise RuntimeError('Password protected PDF unsupported')
            if len(doc)>max_pages:manifest['warnings'].append('Only first %d of %d pages processed'%(max_pages,len(doc)))
            for i in range(min(len(doc),max_pages)):
                page=doc[i];scale=dpi/72
                if page.rect.width*page.rect.height*scale**2>MAX_PIXELS:raise ValueError('PDF render exceeds image pixel limit')
                pix=page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=True)
                img=assets/f'page_{i+1:03d}.png';pix.save(img)
                svg=assets/f'page_{i+1:03d}.svg'
                # PDF-native vector/text data preserved as SVG rather than raster tracing.
                svg.write_text(page.get_svg_image(text_as_path=False),encoding='utf-8')
                text_file=assets/f'page_{i+1:03d}_text.json'
                spans=[]
                for block in page.get_text('dict')['blocks']:
                    if 'lines' in block:
                        for line in block['lines']:
                            for span in line['spans']:
                                spans.append({k:span.get(k) for k in ('text','bbox','font','size','color')})
                text_file.write_text(json.dumps(spans,ensure_ascii=False,indent=2),encoding='utf-8')
                if audit_text(spans)['has_rtl_script']:
                    manifest['warnings'].append(f'Page {i+1}: Arabic/Hebrew text detected; verify bidi shaping and fonts in Adobe.')
                manifest['artboards'].append({'name':f'page_{i+1:03d}','width':pix.width,'height':pix.height,'width_pt':page.rect.width,'height_pt':page.rect.height,'preview':str(img),'svg':str(svg),'text_metadata':str(text_file),'language_audit':audit_text(spans),'layers':[{'name':'PDF page composite','image':str(img),'x':0,'y':0,'mode':'raster'}],'source_type':'pdf'})
        finally:doc.close()
    elif ext=='.psd':
        from psd_tools import PSDImage
        psd=PSDImage.open(source)
        if psd.width*psd.height>MAX_PIXELS:raise ValueError('PSD too large')
        layers=[]
        for n,layer in enumerate(psd.descendants()):
            if layer.kind=='group':continue
            try:
                im=layer.composite()
                if im is None:continue
                fp=assets/f'layer_{n:03d}_{slug(layer.name)}.png'
                im.convert('RGBA').save(fp)
                layers.append({'name':layer.name,'kind':layer.kind,'image':str(fp),'x':int(layer.left),'y':int(layer.top),'visible':bool(layer.is_visible()),'mode':'raster','source_editability':'rendered_extraction_only'})
            except Exception as e:manifest['warnings'].append(f'Layer {n}: {type(e).__name__}: {str(e)[:140]}')
        composite=psd.composite()
        if composite is None:raise ValueError('PSD composite not available')
        preview=assets/'composite.png';composite.convert('RGBA').save(preview)
        if not layers:layers=[{'name':'PSD composite','image':str(preview),'x':0,'y':0,'mode':'raster'}]
        manifest['artboards'].append({'name':'artboard_001','width':psd.width,'height':psd.height,'preview':str(preview),'layers':layers,'source_type':'psd'})
        manifest['warnings'].append('PSD extracted layer pixels do not preserve editable type, shape, adjustment, or smart objects.')
    else:
        with Image.open(source) as original:
            original.load()
            im=original.convert('RGBA')
        if im.width*im.height>MAX_PIXELS:raise ValueError('Image too large')
        preview=assets/'composite.png';im.save(preview)
        board={'name':'artboard_001','width':im.width,'height':im.height,'preview':str(preview),'layers':[{'name':'Flattened source (no inferred layers)','image':str(preview),'x':0,'y':0,'mode':'raster'}],'source_type':'image'}
        manifest['artboards'].append(board)
        manifest['warnings'].append('Flat images have no extractable text metadata; multilingual text reconstruction requires OCR (not included).')
        manifest['warnings'].append('Flat image: object-aware layer separation is NOT performed in core. Use optional segmentation adapter later.')
        if vectorize:
            try:
                import vtracer
                fp=assets/'traced.svg';vtracer.convert_file(str(preview),str(fp));board['svg']=str(fp)
                manifest['warnings'].append('Traced SVG contains approximated paths; original editable fonts and shapes are not recovered.')
            except Exception as e:manifest['warnings'].append('Vector tracing unavailable: '+str(e)[:180])
    # Resize raster layers in conservative mode: NEAREST/BICUBIC is NOT AI super-resolution.
    if upscale!=1:
        manifest['warnings'].append('Upscale option performs Lanczos resizing only; it is NOT AI super-resolution or detail recovery.')
        for board in manifest['artboards']:
            w,h=board['width'],board['height']
            if w*h*upscale*upscale>MAX_PIXELS:
                manifest['warnings'].append('Upscale skipped for '+board['name']+': pixel guard')
                continue
            with Image.open(board['preview']) as im:
                fp=assets/(board['name']+'_resized.png')
                im.resize((w*upscale,h*upscale),Image.Resampling.LANCZOS).save(fp)
                board['resized_preview']=str(fp)
    file=out/'manifest.json';file.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return file

def validate(path):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    issues=[]
    if data.get('schema')!='ali/1':issues.append('invalid schema')
    for board in data.get('artboards',[]):
        if board['width']<1 or board['height']<1:issues.append('invalid board dimensions')
        for field in ('preview','svg','text_metadata'):
            if field in board and not Path(board[field]).is_file():issues.append(f'missing {field}: {board[field]}')
        for layer in board['layers']:
            if not Path(layer['image']).is_file():issues.append('missing layer '+layer['image'])
    return {'ok':not issues,'issues':issues,'artboards':len(data.get('artboards',[])),'warnings':data.get('warnings',[])}
