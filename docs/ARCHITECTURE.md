# Architecture and delivery contract

```text
CLI / Claude Code / Cursor Skill
      |
      v
Ingestion & safeguards (type, pixel limits, SHA256)
      |
      +-- JPG/PNG -> RGBA copy -> [optional VTracer] -> SVG
      +-- PDF -> PyMuPDF -> native text SVG + text spans + raster
      +-- PSD -> psd-tools -> rendered layers + composite
      |
      v
manifest.json + assets/ + warnings
      |
      +-- Photoshop JSX (composited pixel layers) -> Photoshop -> PSD
      +-- Illustrator JSX (SVG open and save) -> Illustrator -> AI
      +-- InDesign JSX (place rendered page) -> InDesign -> INDD
      |
      v
Adobe open/review/save + visual QA + delivery
```

## Manifest contract
`schema: ali/1`; full input SHA256, timestamp, artboards, absolute asset paths, pixel dimensions, warnings, source types. Absolute paths intentionally bind generated scripts to the local machine; copy/move needs re-generation.

## Input semantics / loss risks
- PDF SVG conversion may drop blend behavior, complex transparencies, layers, fonts or interactions; comparing a reference raster is mandatory.
- PSD previews are composited assets; layer bounds, effects and masks may be incomplete. Original PSD stays authoritative.
- Flat source has no knowable original layers. Do not invent a layered reconstruction.
- Image-traced SVG curves are not the designer's original vector geometry and may create thousands of nodes.
- InDesign output is an editable *placed graphic frame*, not reconstructed typography.
- Photoshop JSX uses legacy ExtendScript for an MVP and requires version-specific testing; recommended production migration is Photoshop UXP `.psjs` with executeAsModal.
- Illustrator and InDesign scripts are reviewable local handoff; Adobe UI version, fonts, permissions and screen dialogs can still break unattended scripting.

## Print processing
Check dimensions, target effective PPI, bleed in mm, CMYK profile handled by Adobe operator; exports here are RGB and do not automatically embed printer ICC profiles.

## Hardware / memory
Default PDF 144 DPI, max 10 pages, 90 MP per raster and render. M2 24GB: stream one page at a time. No heavy diffusion model in default environment.
