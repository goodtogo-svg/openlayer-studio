# Test and acceptance gates

## Component quality gates (mandatory, effective 2026-11-07)

- Follow [部件最低品質要求 CQ-01..CQ-09](COMPONENT-QUALITY-REQUIREMENTS-zh-TW.md). The studio-designated quality reference (see 「設定品質基準」 in that document) establishes the minimum visual usability threshold, adapted to each component's original style.
- Independently review structural continuity, geometry/pose, concealed surfaces, intended openings, material detail and usable alpha. A mask cannot be its own sole ground truth.
- Preserve clean component masters separately from effects and environment masks. Inspect them without glow, foreground objects or reference layers concealing defects.
- Compare on gray/light/dark backgrounds, inspect at 100% and 400%, and check final composition anchors, lighting, contact and occlusion. Keep evidence in jobs/ and reports/.
- Record each CQ gate as pass/fail/not_assessed using the [review template](component-quality-review-template.json). Failed or unassessed quality gates prevent a quality-complete claim; report candidates and corrections explicitly. Agent review is not user approval.
- Automated tests, native-file reopening and visual quality are independent gates; all applicable gates are required. Existing outputs do not automatically pass because these requirements were added.

## Automated gates (required)
- `python -m compileall -q src`
- `pytest -q`
- `ali doctor` shows every required import `true`.
- `ali process` returns absolute path to JSON manifest.
- `ali validate <manifest>` returns `ok: true`; missing assets fail.
- Ingest JPG, PNG with alpha, 2-page PDF, and a layered PSD supplied by operator.
- SVG is nonempty with `<svg`; traced image visuals compared against original.
- source SHA256 recorded, originals unchanged.
- repeated batch runs use unique folders; nonzero failures logged.

## Adobe human-in-the-loop gates (must run on actual installed apps)
- Photoshop: open resulting PSD, inspect layer count/names and positions, compare against preview; record screenshot.
- Illustrator: open resulting AI and check paths remain selectable; compare SVG rendering and original at 100% / 400%.
- InDesign: open INDD, verify linked/placed graphics, page size, preflight warnings and fonts.
- Verify source-vs-output preview visually and record differences; commercial release needs approved proof.

## Limits (NOT pass claims)
- Fully automatic faithful PDF-to-editable-InDesign reconstruction: incomplete; approved article/story workflows do not prove universal reconstruction.
- Universal source-faithful PNG semantic separation, complete clean materials and original filter recovery: incomplete; individual engine or case-study success must not be generalized.
- Legacy process --upscale is Lanczos, not AI. Neural SR requires an installed engine/model and actual inference verification.
- Native Adobe automatic export in a headless environment: NOT IMPLEMENTED.
- Supervised Adobe scripting requires an installed application and session authorization; a headless Adobe/UXP runtime is not implemented.
