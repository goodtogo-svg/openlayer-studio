"""Non-translating Unicode-script audit. Script labels are NOT language identification."""
from collections import Counter
import unicodedata

SUPPORTED_LOCALES = ('auto', 'zh-TW', 'zh-CN', 'en', 'ja', 'ko', 'fr', 'de', 'es', 'ar', 'he', 'th', 'vi')

def script_counts(text):
    counts = Counter()
    for ch in text:
        if not ch.isalpha():
            continue
        code = ord(ch)
        if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
            script = 'Han (shared by Chinese/Japanese)'
        elif 0x3040 <= code <= 0x30FF:
            script = 'Japanese kana'
        elif 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF:
            script = 'Hangul'
        elif 0x0600 <= code <= 0x06FF:
            script = 'Arabic'
        elif 0x0590 <= code <= 0x05FF:
            script = 'Hebrew'
        elif 0x0E00 <= code <= 0x0E7F:
            script = 'Thai'
        elif 'LATIN' in unicodedata.name(ch, ''):
            script = 'Latin'
        else:
            script = 'Other'
        counts[script] += 1
    return dict(counts)

def audit_text(spans):
    texts = [s.get('text', '') for s in spans]
    counts = script_counts('\n'.join(texts))
    rtl = bool(counts.get('Arabic') or counts.get('Hebrew'))
    return {'scripts': counts, 'has_rtl_script': rtl, 'text_spans': len(spans),
            'notes': 'Unicode script detection only; no language translation or OCR is performed.'}
