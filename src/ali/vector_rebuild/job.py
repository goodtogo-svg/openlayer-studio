"""`ali vector-rebuild`: image -> OCR + tiled segmentation -> parametric vector rebuild (SVG, PDF, PDF-compatible .ai).

The host side checks the source, writes request.json and runs the worker in the segmentation environment
(torch, SAM 2, OpenCV, PyMuPDF, cairosvg, fontTools). The worker calls the OCR environment for text.
The source is never modified; its SHA256 is recorded before and checked after the run.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_FONTS = [
    {'variable': 'models/fonts/NotoSansTC.ttf'},
    {'file': '/usr/share/fonts/opentype/inter/Inter-Bold.otf'},
    {'file': '/usr/share/fonts/opentype/inter/Inter-Black.otf'},
    {'file': '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf', 'postscript': 'Arial-BoldMT'},
]


def parse_font_args(values, root):
    """--font PATH[=PostScriptName] and --font-variable PATH -> font config; default list when none given."""
    config = []
    for v in values or []:
        kind, _, rest = v.partition(':')
        if kind == 'variable':
            config.append({'variable': rest})
        else:
            path, _, ps = v.partition('=')
            config.append({'file': path, **({'postscript': ps} if ps else {})})
    if not config:
        config = DEFAULT_FONTS
    out = []
    for item in config:
        key = 'variable' if 'variable' in item else 'file'
        p = Path(item[key]).expanduser()
        if not p.is_absolute():
            p = Path(root) / p
        if p.is_file():
            out.append(dict(item, **{key: str(p)}))
    if not out:
        raise FileNotFoundError('No usable fonts; pass --font PATH')
    return out


def run_job(source, out, locale='auto', fonts=None, pt_per_px=None, timeout=3600):
    from ..runtime import ROOT, digest
    source = Path(source).expanduser().resolve()
    out = Path(out).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if out == source.parent or out in source.parents:
        raise ValueError('Output overlaps source')
    if out.exists() and any(out.iterdir()):
        raise FileExistsError('Output is not empty; choose a new folder')
    python = ROOT / 'models/envs/segmentation/bin/python'
    ocr_python = ROOT / 'models/envs/ocr/bin/python'
    for p, name in ((python, 'segmentation'), (ocr_python, 'ocr')):
        if not p.is_file():
            raise RuntimeError('Missing isolated environment: %s; run scripts/setup-v04.py %s' % (name, name))
    out.mkdir(parents=True, exist_ok=True)
    request = {'schema': 'ali-vector-rebuild/1', 'source': str(source), 'source_sha256': digest(source), 'out': str(out),
               'locale': locale, 'fonts': parse_font_args(fonts, ROOT), 'pt_per_px': pt_per_px,
               'ocr_python': str(ocr_python)}
    (out / 'request.json').write_text(json.dumps(request, ensure_ascii=False, indent=2))
    env = os.environ.copy()
    env.update(PYTHONPATH=str(ROOT / 'src'), PYTHONDONTWRITEBYTECODE='1', PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK='True',
               HF_HOME=str(ROOT / 'models/huggingface'), PADDLE_PDX_CACHE_HOME=str(ROOT / 'models/paddlex'))
    started = time.time()
    with open(out / 'run.log', 'a') as log:
        proc = subprocess.run([str(python), '-m', 'ali.vector_rebuild.job', str(out / 'request.json')],
                              stdout=log, stderr=subprocess.STDOUT, env=env, timeout=timeout)
    if digest(source) != request['source_sha256']:
        raise RuntimeError('Source changed during the run')
    if proc.returncode:
        raise RuntimeError('vector-rebuild failed (exit %d); see %s' % (proc.returncode, out / 'run.log'))
    report = json.loads((out / 'rebuild-report.json').read_text())
    artifacts = {p.name: digest(p) for p in sorted(out.iterdir()) if p.suffix in ('.svg', '.pdf', '.ai')}
    return {'status': 'complete', 'source_sha256': request['source_sha256'], 'out': str(out), 'artifacts': artifacts,
            'elapsed_seconds': round(time.time() - started, 1), 'text_frames': report['text_frames'],
            'layers': report['layers'], 'region_stats': report['region_stats'], 'score': report['score'],
            'note': 'Not user-approved. A native .ai counts only after it is opened and saved in Illustrator.'}


def guess_locale(records):
    cjk = sum(1 for r in records for ch in r['text'] if ord(ch) >= 0x2e80)
    return 'zh-TW' if cjk else 'en'


def worker(request_path):
    import numpy as np
    from PIL import Image
    from . import segment
    from .arcs import subprocess_ocr
    from .build import rebuild
    req = json.loads(Path(request_path).read_text())
    out = Path(req['out'])
    im = Image.open(req['source'])
    if im.mode != 'RGB':
        im = im.convert('RGBA')
        flat = Image.new('RGBA', im.size, (255, 255, 255, 255))
        flat.alpha_composite(im)
        im = flat
    src = np.array(im.convert('RGB'))
    H, W = src.shape[:2]
    ocr = subprocess_ocr(req['ocr_python'], out / 'work' / 'ocr')
    records = ocr([src])[0]
    (out / 'ocr.json').write_text(json.dumps(records, ensure_ascii=False))
    locale = req['locale'] if req['locale'] != 'auto' else guess_locale(records)
    print('ocr: %d lines, locale %s' % (len(records), locale), flush=True)
    masks = segment.tiled_masks(src, log=lambda s: print(s, flush=True))
    label = segment.partition(segment.merge_masks(masks, H, W), H, W)
    np.save(out / 'work' / 'label.npy', label)
    print('segmentation: %d regions' % (label.max() + 1), flush=True)
    rebuild(src, records, label, req['fonts'], out, pt_per_px=req['pt_per_px'], locale=locale, ocr=ocr,
            log=lambda s: print(s, flush=True))


if __name__ == '__main__':
    worker(sys.argv[1])
