"""Explicitly licensed font catalog; no implicit scan of commercial fonts."""
import json
from pathlib import Path
from ali.runtime import atomic_json,digest

def build_index(catalog,out):
 from fontTools.ttLib import TTFont
 catalog=Path(catalog).resolve();data=json.loads(catalog.read_text());records=[]
 for entry in data['fonts']:
  fontpath=(catalog.parent/entry['path']).resolve();licensepath=(catalog.parent/entry['license_file']).resolve()
  if entry['license'] not in ('OFL-1.1','Apache-2.0'):raise ValueError('Only explicitly open-licensed candidates allowed')
  if not licensepath.is_file():raise ValueError('Missing license evidence')
  with TTFont(fontpath,fontNumber=entry.get('face_index',0)) as font:
   axes={a.axisTag:[a.minValue,a.defaultValue,a.maxValue] for a in font['fvar'].axes} if 'fvar' in font else {}
   records.append({'path':str(fontpath),'sha256':digest(fontpath),'face_index':entry.get('face_index',0),'family':font['name'].getBestFamilyName(),'weight':font['OS/2'].usWeightClass,'axes':axes,'codepoints':sorted(font.getBestCmap() or {}),'license':entry['license'],'license_file':str(licensepath),'license_sha256':digest(licensepath)})
 atomic_json(out,{'schema':'ali-font-index/1','fonts':records});return {'indexed_faces':len(records),'index':str(out)}
