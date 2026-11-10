"""Local model resources, bounded workers, durable task state and provenance."""
import hashlib
import json
import os
import platform
import resource
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = {
 'inpaint-lama': {'engine':'inpaint','file':'big-lama.pt','url':'https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt','license':'Apache-2.0 code; weights trained on Places365 (non-commercial research terms)','usage':'research-only','md5':'e3aa4aaa15225a33ec84f9f4bc47e500'},
 'sam2-tiny': {'engine':'segmentation','file':'sam2.1_hiera_tiny.pt','url':'https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt','license':'Apache-2.0','config':'configs/sam2.1/sam2.1_hiera_t.yaml'},
 'sam2-small': {'engine':'segmentation','file':'sam2.1_hiera_small.pt','url':'https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt','license':'Apache-2.0','config':'configs/sam2.1/sam2.1_hiera_s.yaml'},
 'sr-photo': {'engine':'sr','file':'RealESRGAN_x4plus.pth','url':'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth','license':'BSD-3-Clause','blocks':23},
 'sr-illustration': {'engine':'sr','file':'RealESRGAN_x4plus_anime_6B.pth','url':'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth','license':'BSD-3-Clause','blocks':6},
}

def digest(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()

def atomic_json(path,data):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)

def hardware():
 result={'system':platform.system(),'machine':platform.machine(),'python':sys.version.split()[0],'memory_bytes':None}
 try:
  if sys.platform=='darwin':result['memory_bytes']=int(subprocess.check_output(['/usr/sbin/sysctl','-n','hw.memsize'],stderr=subprocess.DEVNULL))
  else:result['memory_bytes']=os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')
 except (OSError,ValueError,subprocess.CalledProcessError):pass
 return result

def model_status(name):
 spec=REGISTRY[name];p=ROOT/'models/weights'/spec['file'];receipt=p.with_suffix(p.suffix+'.json')
 if not p.is_file() or not receipt.is_file():return {'name':name,'ready':False,'reason':'missing checkpoint or integrity receipt'}
 meta=json.loads(receipt.read_text());ok=meta.get('sha256')==digest(p) and meta.get('url')==spec['url']
 return {'name':name,'ready':ok,'path':str(p),'reason':None if ok else 'checkpoint checksum/provenance mismatch'}

def fetch_model(name,accept_research_license=False):
 spec=REGISTRY[name]
 if spec.get('usage')=='research-only' and not accept_research_license:
  raise PermissionError(f'{name} is research-only ({spec["license"]}). Re-run with --accept-research-license if your use qualifies; see NOTICE.md.')
 p=ROOT/'models/weights'/spec['file'];p.parent.mkdir(parents=True,exist_ok=True)
 if p.exists():
  status=model_status(name)
  if status['ready']:return status
  raise FileExistsError('Existing unverified model; refusing overwrite: '+str(p))
 part=p.with_suffix(p.suffix+'.part')
 req=urllib.request.Request(spec['url'],headers={'User-Agent':'ALI/0.4'})
 with urllib.request.urlopen(req,timeout=90) as src,open(part,'wb') as dest:
  total=0
  while chunk:=src.read(1024*1024):
   total+=len(chunk)
   if total>1024**3:raise ValueError('Model download exceeds 1 GiB guard')
   dest.write(chunk)
 if total<1024:raise ValueError('Invalid checkpoint download')
 if spec.get('md5') and hashlib.md5(part.read_bytes()).hexdigest()!=spec['md5']:raise ValueError('Upstream model MD5 mismatch')
 part.replace(p);atomic_json(p.with_suffix(p.suffix+'.json'),{'url':spec['url'],'sha256':digest(p),'license':spec['license'],'usage':spec.get('usage','see license'),'trust':'first-download digest, not upstream-signed checksum'})
 return model_status(name)

def run_engine(engine,source,out,options=None,resume=False,timeout=900):
 source=Path(source).expanduser().resolve();out=Path(out).resolve();options=options or {}
 if not source.is_file():raise FileNotFoundError(source)
 if out==source or out in source.parents:raise ValueError('Output overlaps source')
 request={'engine':engine,'source':str(source),'source_sha256':digest(source),'out':str(out),'options':options}
 request['auxiliary_sha256']={key:digest(Path(options[key])) for key in ('prompts','index','mask') if options.get(key)}
 fingerprint=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest();statefile=out/'state.json'
 out.mkdir(parents=True,exist_ok=True)
 # Exclusive lock protects state and partial assets from concurrent resume.
 lock=out/'.running'
 if lock.exists() and resume:
  try:os.kill(int(lock.read_text()),0)
  except ProcessLookupError:lock.unlink()
  except (ValueError,PermissionError):raise RuntimeError('Cannot verify stale lock; inspect process before removing it')
 fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
 try:
  os.write(fd,str(os.getpid()).encode());os.close(fd)
  if statefile.exists():
   prev=json.loads(statefile.read_text())
   if not resume:raise FileExistsError('Job exists; use --resume with identical input/options')
   if prev.get('fingerprint')!=fingerprint:raise ValueError('Resume input/options changed')
   if prev.get('status')=='complete':
    valid=all(Path(p).is_file() and digest(p)==sha for p,sha in prev['artifacts'].items())
    if valid:return prev
  elif any(p.name!='.running' for p in out.iterdir()):raise FileExistsError('Output is not an empty job')
  state={'schema':'ali-task/1','fingerprint':fingerprint,'request':request,'status':'running','started':time.time(),'hardware':hardware()};atomic_json(statefile,state)
  python=ROOT/'models/envs'/engine/'bin/python'
  try:
   if not python.is_file():raise RuntimeError(f'Missing isolated environment: {engine}; run scripts/setup-v04.py {engine}')
   atomic_json(out/'request.json',request)
   env=os.environ.copy();env['PYTHONPATH']=str(ROOT/'src');env['PYTHONDONTWRITEBYTECODE']='1';env['OMP_NUM_THREADS']='1' if engine=='ocr' else '4';env['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK']='True';env['HF_HOME']=str(ROOT/'models/huggingface');env['PADDLE_PDX_CACHE_HOME']=str(ROOT/'models/paddlex')
   with open(out/'inference.log','a') as log:
    proc=subprocess.run([str(python),'-m','ali.engines.worker',str(out/'request.json')],stdout=log,stderr=subprocess.STDOUT,env=env,timeout=timeout)
   if proc.returncode:raise RuntimeError(f'{engine} failed (exit {proc.returncode}); see {out}/inference.log')
   result=json.loads((out/'result.json').read_text())
   if result.get('status')!='complete':raise RuntimeError('Worker did not confirm completion')
   artifacts={str(p):digest(p) for p in out.rglob('*') if p.is_file() and p.name not in ('state.json','inference.log','.running')}
   if digest(source)!=request['source_sha256']:raise RuntimeError('Source changed during inference')
   state.update(status='complete',result=result,artifacts=artifacts)
  except Exception as exc:
   state.update(status='failed',error=f'{type(exc).__name__}: {exc}')
  state['elapsed_seconds']=time.time()-state['started'];atomic_json(statefile,state)
  if digest(source)!=request['source_sha256']:raise RuntimeError('Source changed during inference')
  if state['status']!='complete':raise RuntimeError(state['error'])
  return state
 finally:
  lock.unlink(missing_ok=True)
