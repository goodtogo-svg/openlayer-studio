> v0.4 更新：下列為原 v0.3 規劃。四個獨立引擎的實作與實測、仍未完成的重建環節，請以 `docs/architecture-v0.4-zh-TW.md` 為準。

# Incremental implementation roadmap

## Phase 1 — shipped in this package
Conservative extraction, per-page SVG, VTracer, source integrity, manifest, Photoshop/Illustrator/InDesign reviewable scripts, tests, batch driver.

## Phase 2 — optional model adapters
Segment Anything 2 inference or rembg for actual object masks, overlap/inpainting, transparent PNG, layer naming with visual verification. Real-ESRGAN via platform-compatible backend. Both behind explicit flags, model registry checks, download consent, deterministic seeds and 24GB budget.

## Phase 3 — editable document rebuild
PDF native text spans + font matching, paragraph styles, editable InDesign text frames, SVG to Illustrator path grouping, PSD masks, embedded ICC profiles, print preflight. Requires Adobe-specific harness and screenshots.

## Phase 4 — reliable automation
Adobe UXP scripts/plugins, macOS automation permissions, application version checks, job queue and retry policy. Approval gate before native execution, verification after export.

## Acceptance principle
Never call phase 2–4 functionality 'implemented' until code, fixtures, test results, and representative Adobe GUI outputs exist.
