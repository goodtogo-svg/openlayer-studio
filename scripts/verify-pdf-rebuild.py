"""Check exported IDML/PDF against a rebuild plan; never claim application success alone."""
import argparse,json,zipfile,xml.etree.ElementTree as ET
from pathlib import Path
import pymupdf
from ali.runtime import atomic_json,digest

p=argparse.ArgumentParser();p.add_argument('plan');p.add_argument('--out',required=True);args=p.parse_args()
plan=json.loads(Path(args.plan).read_text());results={'source_unchanged':digest(plan['source'])==plan['source_sha256'],'application_verified':False,'limitations':plan['limitations']}
expected=sum(p['editable_count'] for p in plan['pages'])
if Path(plan['expected_idml']).exists():
 with zipfile.ZipFile(plan['expected_idml']) as z:
  storyfiles=[n for n in z.namelist() if n.startswith('Stories/') and n.endswith('.xml')]
  contents=[]
  for n in storyfiles:
   tree=ET.fromstring(z.read(n));contents.append(''.join(node.text or '' for node in tree.iter('Content')))
  planned=[s['text'] for page in plan['pages'] for s in page['editable']]
  from collections import Counter
  results['idml']={'stories':len(storyfiles),'expected_text_frames':expected,'text_multiset_matches':Counter(contents)==Counter(planned),'unicode_characters':sum(map(len,contents))}
if Path(plan['expected_proof']).exists() and Path(plan['expected_proof']).stat().st_size>0:
 with pymupdf.open(plan['expected_proof']) as doc:
  results['proof_pdf']={'pages':len(doc),'trim_sizes':[[page.trimbox.width,page.trimbox.height] for page in doc]}
metrics=Path(plan['script']).parent/'reopened-verification.json'
if metrics.exists():
 results['native_reopen']=json.loads(metrics.read_text());results['application_verified']=results['native_reopen'].get('saved_and_reopened') is True
native=results.get('native_reopen',{})
proof=results.get('proof_pdf',{})
expected_sizes=[[p['width_pt'],p['height_pt']] for p in plan['pages']]
proof_ok=proof.get('pages')==len(expected_sizes) and all(abs(a-b)<.01 for size,expected_size in zip(proof.get('trim_sizes',[]),expected_sizes) for a,b in zip(size,expected_size))
native_ok=(native.get('pages')==len(expected_sizes) and native.get('text_frames')==expected and native.get('overset')==0 and native.get('bad_fonts')==0 and native.get('bad_links')==0 and native.get('max_baseline_error_pt',999)<.01)
results['checks']={'proof_dimensions':proof_ok,'native_geometry_fonts_and_overflow':native_ok}
results['status']='passed_with_review_required' if results['source_unchanged'] and results.get('idml',{}).get('text_multiset_matches') and results['application_verified'] and proof_ok and native_ok else 'pending_or_failed' 
atomic_json(args.out,results);print(json.dumps(results,ensure_ascii=False,indent=2))

raise SystemExit(0 if results['status']=='passed_with_review_required' else 2)
