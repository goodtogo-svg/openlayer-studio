import importlib
import json
import resource
import sys
import time
from pathlib import Path
from ali.runtime import atomic_json

def main():
 request=json.loads(Path(sys.argv[1]).read_text());start=time.time()
 module=importlib.import_module('ali.engines.'+request['engine'])
 result=module.infer(request)
 result.update(status='complete',engine=request['engine'],elapsed_seconds=time.time()-start,peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024))
 atomic_json(Path(request['out'])/'result.json',result)
if __name__=='__main__':main()
