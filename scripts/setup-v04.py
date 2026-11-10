"""Create isolated engine environments; never change system Python."""
import argparse, os, subprocess, sys
from pathlib import Path
if sys.version_info<(3,11):raise SystemExit('Use project Python >=3.11')
p=argparse.ArgumentParser();p.add_argument('engine',choices=['segmentation','ocr','sr','fonts','inpaint']);a=p.parse_args()
root=Path(__file__).resolve().parents[1];env=root/'models/envs'/a.engine
subprocess.run([sys.executable,'-m','venv',str(env)],check=True)
envvars=os.environ.copy();envvars['SAM2_BUILD_CUDA']='0';envvars['PIP_CACHE_DIR']=str(root/'models/pip-cache')
resolved=root/'models/locks'/f'{a.engine}-resolved.txt'
requirements=resolved if resolved.exists() else root/'models/locks'/f'{a.engine}.txt'
# sr: install the fully pinned list without resolver-added extras. realesrgan declares
# gfpgan/facexlib (face restoration, not used here, non-commercial components), so they are left out.
extra=['--no-deps'] if a.engine=='sr' and requirements==resolved else []
subprocess.run([str(env/'bin/python'),'-m','pip','install',*extra,'-r',str(requirements)],env=envvars,check=True)
r=subprocess.run([str(env/'bin/python'),'-m','pip','freeze'],capture_output=True,text=True,check=True)
(root/'models/locks'/f'{a.engine}-resolved.txt').write_text(r.stdout)
