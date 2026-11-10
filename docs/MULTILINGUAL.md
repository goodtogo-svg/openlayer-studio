# Multilingual design pipeline (v0.2)

## Scope
Supported locale metadata: auto, zh-TW, zh-CN, en, ja, ko, fr, de, es, ar, he, th, vi. This is an extensible UI/production locale list, not a claim of translation or exact recognition. All manifests and PDF text metadata are UTF-8.

## Existing features
1. `ali process --locale` stores project locale in `settings.locale`.
2. PDF extraction preserves Unicode text spans and source font metadata, with script-count audit. No OCR is involved.
3. Arabic/Hebrew character ranges trigger a right-to-left QA warning. No automatic RTL shaping or layout.
4. Agent instructions support English and Traditional Chinese prompts (and other languages through the agent), without changing stored original text.

## Proposed next features (not implemented)
- Optional Tesseract/PaddleOCR plug-ins with explicit language packs and confidence/region details; compare against raster proof.
- OCR reading-order reconstruction for multi-column and mixed-language pages; avoid destructive edits.
- Editable text-frame generation with font substitution report for Photoshop/Illustrator/InDesign, following actual Adobe script/API capabilities.
- Locale-specific paragraph rules, Japanese vertical text, BiDi shaping, and approved translation input / review workflow.
- Fonts report: enumerate missing glyphs and substituted fonts using app-side validation, rather than guessing from Python.
- Test fixtures for zh-TW, zh-CN, English, Japanese, Korean, mixed bilingual text, Arabic/Hebrew, emoji and combining diacritics.

## Acceptance gates
- No Unicode replacement characters introduced in JSON exports.
- Displayed text matches source proof: text, line wrapping, font, direction, spacing, RTL, punctuation, typography.
- For mixed-language and translated editions, every language requires approved copy and a human or app-level proof before print release.
- Do not claim editable Adobe type has been recovered unless inspected in the native Adobe application.

## Font inventory implemented in v0.3
Run `ali fonts jobs/<job>/manifest.json --out jobs/<job>/font_report.json` for conservative PDF-font name matching and free-source recommendations. This is not visual font recognition, and its output is not a guarantee of glyph coverage or identity. See `docs/FONTS.md`.
