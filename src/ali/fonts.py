"""Offline local-font inventory and conservative suggestions from freely licensed font families.

No network download, no automatic font installation, no image font-identification claims.
"""
from pathlib import Path
from difflib import SequenceMatcher
import json, re

# Curated font families with reputable open-source distribution; individual local files
# must be license-checked before redistribution or embedding. Links are for manual review.
OPEN_FONTS = {
 'zh-TW': [('Noto Sans TC','https://github.com/notofonts/noto-cjk'),('Noto Serif TC','https://github.com/notofonts/noto-cjk'),('Source Han Sans TC','https://github.com/adobe-fonts/source-han-sans')],
 'zh-CN': [('Noto Sans SC','https://github.com/notofonts/noto-cjk'),('Noto Serif SC','https://github.com/notofonts/noto-cjk'),('Source Han Sans SC','https://github.com/adobe-fonts/source-han-sans')],
 'ja': [('Noto Sans JP','https://github.com/notofonts/noto-cjk'),('Noto Serif JP','https://github.com/notofonts/noto-cjk')],
 'ko': [('Noto Sans KR','https://github.com/notofonts/noto-cjk'),('Noto Serif KR','https://github.com/notofonts/noto-cjk')],
 'ar': [('Noto Naskh Arabic','https://github.com/notofonts/arabic'),('Noto Sans Arabic','https://github.com/notofonts/arabic')],
 'he': [('Noto Sans Hebrew','https://github.com/notofonts/hebrew')],
 'th': [('Noto Sans Thai','https://github.com/notofonts/thai')],
 'vi': [('Noto Sans','https://github.com/notofonts/latin-greek-cyrillic'),('Inter','https://github.com/rsms/inter')],
 'en': [('Inter','https://github.com/rsms/inter'),('Noto Sans','https://github.com/notofonts/latin-greek-cyrillic')],
 'fr': [('Inter','https://github.com/rsms/inter'),('Noto Sans','https://github.com/notofonts/latin-greek-cyrillic')],
 'de': [('Inter','https://github.com/rsms/inter'),('Noto Sans','https://github.com/notofonts/latin-greek-cyrillic')],
 'es': [('Inter','https://github.com/rsms/inter'),('Noto Sans','https://github.com/notofonts/latin-greek-cyrillic')],
 'auto': [('Noto Sans','https://github.com/notofonts/latin-greek-cyrillic'),('Noto Sans TC','https://github.com/notofonts/noto-cjk')],
}

def _key(name):
    name = re.sub(r'^[A-Z]{6}\+', '', str(name or '')) # PDF embedded font subset prefix
    name = re.sub(r'(?i)[-_ ](regular|bold|medium|italic|light|semibold)$', '', name)
    return re.sub(r'[^a-z0-9]', '', name.casefold())

def list_local_fonts(extra_dirs=()):
    """Scan font name tables; returns paths solely for locally installed fonts."""
    try:
        from fontTools.ttLib import TTFont, TTCollection
    except ImportError as exc:
        raise RuntimeError('Install fontTools: python -m pip install -e ".[fonts]"') from exc
    dirs=[Path.home()/'Library/Fonts',Path('/Library/Fonts'),Path('/System/Library/Fonts'),*map(Path,extra_dirs)]
    seen=set();result=[]
    for directory in dirs:
        if not directory.exists():continue
        for path in directory.rglob('*'):
            if path.suffix.lower() not in ('.ttf','.otf','.ttc','.otc') or path in seen:continue
            seen.add(path)
            try:
                collection=TTCollection(str(path),lazy=True) if path.suffix.lower() in ('.ttc','.otc') else None
                fonts=collection.fonts if collection else [TTFont(str(path),lazy=True)]
                for index,font in enumerate(fonts):
                    try:
                        if 'name' not in font:continue
                        table=font['name'];family=table.getBestFamilyName() or table.getDebugName(1)
                        full=table.getDebugName(4) or family
                        if family:result.append({'family':family,'full_name':full,'file':str(path),'face_index':index})
                    finally:font.close()
            except Exception:continue
    return sorted(result,key=lambda x:(x['family'].casefold(),x['file'],x['face_index']))

def compare_fonts(source_names, locale='auto', inventory=None):
    """Match font *names*; do not confuse a similar name with visual font identification."""
    if locale not in OPEN_FONTS:raise ValueError('Unsupported locale')
    installed=list_local_fonts() if inventory is None else inventory
    by_key={}
    for item in installed:
        for name in (item.get('family'),item.get('full_name')):
            if name:by_key.setdefault(_key(name),item)
    recommendations=[]
    for name,url in OPEN_FONTS[locale]:
        recommendations.append({'family':name,'source':url,'installed':_key(name) in by_key,'license_note':'Review exact font release license before distributing font files'})
    matches=[]
    for source in sorted(set(str(x) for x in source_names if x)):
        key=_key(source);exact=by_key.get(key)
        # Best-effort shortlist for operator; similarity must never be treated as verified font match.
        similar=sorted(installed,key=lambda x:SequenceMatcher(None,key,_key(x['family'])).ratio(),reverse=True)[:3] if not exact else []
        matches.append({'source_font':source,'installed_exact_name':bool(exact),'installed_font':exact,
                        'similar_installed_families':[x['family'] for x in similar],
                        'identity_verified':False,'note':'PDF metadata / filename is not visual glyph identification'})
    return {'locale':locale,'checked_local_font_faces':len(installed),'source_fonts':matches,
            'free_font_recommendations':recommendations,
            'limitations':['No OCR or image-based font classifier','No proof of identical glyph shape or metrics',
                           'Font substitution may change line breaks and CJK/RTL shaping',
                           'OFL and other licenses must be checked for redistribution/embedding']}

def report_for_manifest(manifest_path, outfile=None, extra_dirs=()):
    manifest_path=Path(manifest_path);manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    names=[]
    for board in manifest.get('artboards',[]):
        textfile=board.get('text_metadata')
        if textfile and Path(textfile).exists():
            spans=json.loads(Path(textfile).read_text(encoding='utf-8'))
            names.extend(s.get('font') for s in spans if s.get('font'))
    report=compare_fonts(names,manifest.get('settings',{}).get('locale','auto'),list_local_fonts(extra_dirs))
    report['source_manifest']=str(manifest_path)
    if outfile:
        out=Path(outfile);out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
