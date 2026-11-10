# Multilingual font analysis — v0.3

## Actually implemented
- `ali fonts manifest.json --out font_report.json` extracts PDF text-span font metadata saved by `ali process` and scans local macOS fonts with `fontTools`.
- Checks exact normalized family/full-name matches, including PDF subset names `ABCDEF+Family`, and provides **unverified** similar name candidates.
- Curated freely available family suggestions by locale: Traditional Chinese `Noto Sans TC`, `Noto Serif TC`, `Source Han Sans TC`; Simplified Chinese SC; Japanese JP; Korean KR; Latin `Inter`/`Noto Sans`; Arabic, Hebrew, Thai Noto families. Listings include authoritative source repositories.
- Preserves original source font metadata. Does not replace fonts automatically.

## NOT implemented
- OCR of JPG/PNG or rasterized PDFs; font classification via visual glyph embedding; detection of exact original font from screenshots.
- Checking full coverage of each text run (`cmap`), CJK variants, all OpenType shaping features or typography metrics. Exact-name match is not a glyph match.
- Auto font downloading, redistribution, installation, Adobe font substitution or editable text-frame reconstruction.

## Future AI font matching algorithm
1. OCR with language-aware text regions and confidence.
2. Normalize orientation, glyph crop and point-size estimates; do not discard original image.
3. Render candidate **locally licensed** fonts with identical Unicode text; compare geometry/perceptual similarity, returning top-N candidates and confidence calibrated on a labeled dataset.
4. Test text coverage and script shaping (`cmap` / HarfBuzz), not just appearance.
5. Present font fallback candidates and image overlay proof for manual review before replacing type.
6. In Adobe, create actual editable text frames and check line wraps, metrics, text overflow and print rights.

## Installation / licensing
Use official GitHub releases or Google Fonts downloads from verified publishers. No typeface files are bundled, as each has its own license and potentially different rules. Fonts under SIL OFL usually permit commercial usage, but exact font license, trademark, redistribution and embedding terms must be reviewed. Do not pretend that an installed proprietary font became free because its name resembles an open font.

## Verification
`pytest -q` covers normalization and metadata audit. On a real Mac, run `ali fonts` and inspect the output against Font Book and Adobe's font menu. For Japanese vertical, Traditional Chinese forms, combining accents and right-to-left text, manually validate the final Adobe proof.
