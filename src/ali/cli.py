import argparse, json, sys
from pathlib import Path
from .pipeline import process,validate
from .adobe import generate
from .language import SUPPORTED_LOCALES

def main(argv=None):
    p=argparse.ArgumentParser(prog='ali',description='OpenLayer Studio preflight')
    sub=p.add_subparsers(dest='cmd',required=True)
    a=sub.add_parser('process');a.add_argument('input');a.add_argument('--out',required=True);a.add_argument('--dpi',type=int,default=144);a.add_argument('--max-pages',type=int,default=10);a.add_argument('--no-vector',action='store_true');a.add_argument('--upscale',type=int,choices=(1,2,4),default=1);a.add_argument('--locale',choices=SUPPORTED_LOCALES,default='auto')
    v=sub.add_parser('validate');v.add_argument('manifest')
    b=sub.add_parser('adobe');b.add_argument('manifest');b.add_argument('--app',choices=('photoshop','illustrator','indesign'),required=True);b.add_argument('--out',required=True)
    f=sub.add_parser('fonts');f.add_argument('manifest');f.add_argument('--out');f.add_argument('--font-dir',action='append',default=[])
    d=sub.add_parser('doctor')
    sub.add_parser('hardware')
    m=sub.add_parser('models');m.add_argument('action',choices=('list','fetch'));m.add_argument('name',nargs='?');m.add_argument('--accept-research-license',action='store_true',help='Allow fetching weights whose terms restrict use to non-commercial research')
    e=sub.add_parser('engine');e.add_argument('engine',choices=('segmentation','ocr','fonts','sr','inpaint'));e.add_argument('input');e.add_argument('--out',required=True);e.add_argument('--options',default='{}',help='JSON options or @JSON-file');e.add_argument('--resume',action='store_true');e.add_argument('--timeout',type=int,default=900)
    r=sub.add_parser('reconstruct');r.add_argument('input');r.add_argument('--out',required=True);r.add_argument('--engines',default='ocr');r.add_argument('--options',default='{}');r.add_argument('--resume',action='store_true')
    fi=sub.add_parser('font-index');fi.add_argument('catalog');fi.add_argument('--out',required=True)
    sc=sub.add_parser('scene-adobe');sc.add_argument('scene');sc.add_argument('--app',choices=('photoshop','illustrator','indesign'),required=True);sc.add_argument('--out',required=True)
    pr=sub.add_parser('pdf-rebuild');pr.add_argument('input');pr.add_argument('--out',required=True);pr.add_argument('--font-map',required=True,help='JSON source-font to installed Adobe font mapping')
    ar=sub.add_parser('article-rebuild',help='One story per article with threaded layout columns');ar.add_argument('input');ar.add_argument('--out',required=True);ar.add_argument('--font-map',required=True);ar.add_argument('--layout',required=True)
    gr=sub.add_parser('graphics-rebuild',help='Separate original image assets from an approved flattened PDF background');gr.add_argument('input',help='Approved INDD');gr.add_argument('--background',required=True);gr.add_argument('--out',required=True);gr.add_argument('--classification',help='JSON illustration_regions in page points')
    ts=sub.add_parser('text-styles');ts.add_argument('input');ts.add_argument('--regions',required=True);ts.add_argument('--out',required=True)
    cm=sub.add_parser("component-master");cm.add_argument("plan");cm.add_argument("--out",required=True)
    se=sub.add_parser("scene-effects");se.add_argument("plan");se.add_argument("--out",required=True)
    vr=sub.add_parser('vector-rebuild',help='Image -> OCR + tiled segmentation -> vector SVG/PDF/.ai with live text');vr.add_argument('input');vr.add_argument('--out',required=True);vr.add_argument('--locale',default='auto');vr.add_argument('--font',action='append',default=[],help='PATH[=PostScriptName] or variable:PATH; repeatable');vr.add_argument('--pt-per-px',type=float);vr.add_argument('--timeout',type=int,default=3600);vr.add_argument('--method',choices=['auto','scene','regions'],default='auto',help='scene: titles, tapes, ribbons as designed objects (accepted 11/08 method); regions: segmentation; auto: scene when it reproduces the image');vr.add_argument('--font-pool',action='append',default=[],help='folder of installed fonts (copies); scene picks the closest face per line group from them');vr.add_argument('--font-names',help="the target app's font names (JSON list or Illustrator app.textFonts dump); pool faces it lacks are skipped");vr.add_argument('--adobe-fonts',help='JSON list of Adobe Fonts faces that replace a free face family (postscript, replaces_family, family, style, url); the free face is the layout proxy, the .ai names the Adobe face')
    args=p.parse_args(argv)
    try:
        if args.cmd in ('engine','reconstruct'):
            from .runtime import run_engine
            raw=args.options
            options=json.loads(Path(raw[1:]).read_text() if raw.startswith('@') else raw)
            if args.cmd=='engine':result=run_engine(args.engine,args.input,args.out,options,args.resume,args.timeout)
            else:
                from .reconstruct import reconstruct
                engines=tuple(x for x in args.engines.split(',') if x)
                if any(x not in ('segmentation','ocr','sr') for x in engines):raise ValueError('Invalid engine list')
                result=reconstruct(args.input,args.out,engines,options,args.resume)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0 if result.get('status')=='complete' else 2
        elif args.cmd=='vector-rebuild':
            from .vector_rebuild.job import run_job
            print(json.dumps(run_job(args.input,args.out,args.locale,args.font,args.pt_per_px,args.timeout,args.method,args.font_pool,args.font_names,args.adobe_fonts),ensure_ascii=False,indent=2))
        elif args.cmd=='scene-effects':
            from .scene_effects import compile_effects
            print(json.dumps(compile_effects(args.plan,args.out),ensure_ascii=False,indent=2))
        elif args.cmd=='component-master':
            from .component_master import compile_master
            print(json.dumps(compile_master(args.plan,args.out),ensure_ascii=False,indent=2))
        elif args.cmd=='text-styles':
            from .text_styles import estimate_report
            print(json.dumps(estimate_report(args.input,args.regions,args.out),ensure_ascii=False,indent=2))
        elif args.cmd=='hardware':
            from .runtime import hardware
            print(json.dumps(hardware(),indent=2))
        elif args.cmd=='models':
            from .runtime import REGISTRY,model_status,fetch_model
            if args.action=='fetch':
                if args.name not in REGISTRY:raise ValueError('Unknown model name')
                result=fetch_model(args.name,accept_research_license=args.accept_research_license)
            else:result=[model_status(n) for n in REGISTRY]
            print(json.dumps(result,indent=2))
        elif args.cmd=='font-index':
            from .font_visual import build_index
            print(json.dumps(build_index(args.catalog,args.out),ensure_ascii=False,indent=2))
        elif args.cmd=='scene-adobe':
            from .scene_adobe import generate_scene
            print(json.dumps(generate_scene(args.scene,args.app,args.out),indent=2))
        elif args.cmd=='graphics-rebuild':
            from .graphics import prepare_graphics
            classification=json.loads(Path(args.classification).read_text()) if args.classification else None
            print(json.dumps(prepare_graphics(args.input,args.background,args.out,classification),ensure_ascii=False,indent=2))
        elif args.cmd=='article-rebuild':
            from .article_flow import build_article_plan
            from .runtime import digest
            source=str(Path(args.input).expanduser().resolve())
            mapping=json.loads(Path(args.font_map).read_text(encoding='utf-8'))
            result=build_article_plan({'source':source,'source_sha256':digest(source),'font_map':mapping},args.layout,args.out)
            print(json.dumps(result,ensure_ascii=False,indent=2))
        elif args.cmd=='pdf-rebuild':
            from .pdf_rebuild import prepare_rebuild
            mapping=json.loads(Path(args.font_map).read_text(encoding='utf-8'))
            print(json.dumps(prepare_rebuild(args.input,args.out,mapping),ensure_ascii=False,indent=2))
        elif args.cmd=='process':print(process(args.input,args.out,dpi=args.dpi,max_pages=args.max_pages,vectorize=not args.no_vector,upscale=args.upscale,locale=args.locale))
        elif args.cmd=='validate':
            result=validate(args.manifest);print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['ok'] else 2
        elif args.cmd=='adobe':print(json.dumps(generate(args.manifest,args.app,args.out),ensure_ascii=False,indent=2))
        elif args.cmd=='fonts':
            from .fonts import report_for_manifest
            print(json.dumps(report_for_manifest(args.manifest,args.out,args.font_dir),ensure_ascii=False,indent=2))
        elif args.cmd=='doctor':
            from importlib.util import find_spec
            print(json.dumps({n:bool(find_spec(n)) for n in ('PIL','pymupdf','psd_tools','vtracer')},indent=2))
        return 0
    except Exception as e:
        print(json.dumps({'ok':False,'error':str(e),'type':type(e).__name__},ensure_ascii=False),file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())
