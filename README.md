# OpenLayer Studio

本機優先的設計稿分層提取與重建工具：把 PNG／JPG／PDF／PSD 拆成可追溯的圖層、遮罩、SVG 與 Scene Graph，再產生在 Photoshop、Illustrator、InDesign 內執行的腳本來重建原生檔。

> **定位：人機協作的重建工具鏈，不是一鍵轉檔器。** 模型推論與輸出都需要操作者審閱與核准。目前只在 macOS Apple Silicon（Python 3.11）測試過。
>
> OpenLayer Studio 是獨立專案，與 Adobe Inc. 無關聯，也未經其認可。Adobe、Photoshop、Illustrator、InDesign、After Effects 是 Adobe Inc. 的商標。產生 PSD／AI／INDD 需要使用者自己持有授權的 Adobe 應用程式。

## 授權

本專案程式碼以 [GNU AGPL-3.0-or-later](LICENSE) 授權。第三方套件、模型權重與字型各有授權，見 [NOTICE.md](NOTICE.md)；權重與字型不隨本專案散布，由安裝指令從官方來源下載。

## 部件品質：強制交付要求

所有部件提取、補全、去特效與 Adobe 重建，必須遵循 [部件最低品質要求](docs/COMPONENT-QUALITY-REQUIREMENTS-zh-TW.md)。最低視覺品質基準由各工作室自行指定一張參考圖，設定方式見該文件的「設定品質基準」。

實體須完整、透視合理、材質與自然明暗保留、透明邊緣可用；不能用光效或遮擋掩蓋幾何瑕疵。遮罩一致、測試通過、Adobe能開啟均不能代替視覺驗收。未達門檻僅能標為候選／待修，不得宣告部件品質完成。詳見 [驗收門檻](docs/ACCEPTANCE.md) 與 [品質審查模板](docs/component-quality-review-template.json)。

**Status:** executable local preflight/extraction MVP + Adobe JSX script generator. Adobe native outputs are *not* produced by Python alone. v0.4 adds isolated SAM 2.1, PaddleOCR, visual font matching and Real-ESRGAN workers. Full automatic editable document reconstruction remains incomplete; see docs/architecture-v0.4-zh-TW.md.

## Quick start (MacBook Pro M2 24GB)
```bash
xcode-select --install # if developer tools are missing
cd openlayer-studio
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
ali doctor
pytest -q
ali process examples/demo.png --out jobs/demo --dpi 144
ali validate jobs/demo/manifest.json
ali adobe jobs/demo/manifest.json --app photoshop --out jobs/demo/photoshop
ali adobe jobs/demo/manifest.json --app illustrator --out jobs/demo/illustrator
ali adobe jobs/demo/manifest.json --app indesign --out jobs/demo/indesign
```

After generating JSX, **open the scripts in installed Adobe applications**:
- Photoshop: File > Scripts > Browse... choose `.jsx` (legacy ExtendScript supported but deprecated; see UXP migration path).
- Illustrator: File > Scripts > Other Script... choose `.jsx`.
- InDesign: Window > Utilities > Scripts, select/run `.jsx` (scripts panel).
- Then open saved PSD/AI/INDD and inspect dimensions, layering, transparency and clipping.

## Sources and capabilities
- JPG/PNG: preserve source as a single RGBA layer; optionally approximate SVG using VTracer. No invented segmentation.
- PDF: per-page raster preview plus SVG with native PDF content/text and JSON text span metadata. Not an exact recreation of all PDF features or a native editable InDesign document.
- PSD: export flattened preview and composited visible/present layer pixels; *not* editable Photoshop type, shape, smart objects or adjustment layers.
- AI: Illustrator must open SVG and save AI. Vector tracing is approximate.
- INDD: InDesign script places a flat composite image on a page. Editable typography/template reconstruction is a separate future feature.

## Installation of Skill
The project contains `.claude/skills/openlayer-studio/SKILL.md` and `.cursor/skills/openlayer-studio/SKILL.md`. Open repository root in Claude Code or Cursor. Both agents can discover the project-level skill. Do not copy only the SKILL.md without this codebase.

## Pilot before unattended use
Runs without user interaction in Python preflight stage, **not** fully unattended Adobe exports. For headless queue see `scripts/batch.sh`; each job logs failures; no automatic native-Adobe launch. See `docs/ARCHITECTURE.md`, `docs/ACCEPTANCE.md`, `docs/ROADMAP.md`.

## Reference projects
- [VTracer](https://github.com/visioncortex/vtracer) (vector trace)
- [psd-tools](https://github.com/psd-tools/psd-tools) (PSD inspect)
- [PyMuPDF](https://pymupdf.readthedocs.io/) (PDF graphics/text extraction)
- [SAM 2](https://github.com/facebookresearch/sam2) (optional future segmentation)
- [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) (optional future neural upscaler)

## Security / privacy
Files remain local by default. No API keys or network processing. Scripts never overwrite source; outputs are per-job. Adobe JSX is generated for review, not executed silently. Check licensing and font embedding permissions before commercial use.

## Multilingual support (v0.2)
The CLI and agent instructions support multilingual projects with `--locale auto|zh-TW|zh-CN|en|ja|ko|fr|de|es|ar|he|th|vi`. The locale is project metadata, **not** an automatic translator, language classifier, OCR engine, or font installer.

```bash
ali process source.pdf --out jobs/taiwan --locale zh-TW
ali process source.pdf --out jobs/multilingual --locale auto
```

- PDF text spans are exported as UTF-8 JSON with their original Unicode text, fonts, sizes and coordinates. Unicode-script counts and Arabic/Hebrew RTL warnings are recorded per page in `manifest.json`.
- Chinese (Traditional/Simplified) is *not* reliably distinguished by Unicode ranges. Han glyphs may also be Japanese; a declared locale must not be treated as a verified language.
- Source JPG/PNG text is pixels: OCR and editable text rebuild are **not yet implemented**. PDF text spans are metadata and **not yet turned into native editable text frames** in PSD/AI/INDD.
- Before printing: check actual font availability, missing glyphs, fallback fonts, CJK line breaks, Japanese vertical writing, Arabic joining/BiDi, character spacing, and font licenses. InDesign generated JSX still places the flat preview.
- For translated editions, keep a separate approved translation/locale input per language; automatic translation is out of scope in v0.2.

See `docs/MULTILINGUAL.md` for a localization/QA roadmap.

## Multilingual free-font audit (v0.3)
This adds an **offline font-name audit** for PDFs (font names embedded in text spans), local font inventory using fontTools, and a curated shortlist of open-source family recommendations. It does **not** identify fonts from pixels, perform OCR, download or install fonts, or rebuild editable Adobe text.

```bash
python -m pip install -e '.[test,fonts]'
ali process layout.pdf --out jobs/layout --locale zh-TW
ali fonts jobs/layout/manifest.json --out jobs/layout/font_report.json
# optional: search an additional local font library
ali fonts jobs/layout/manifest.json --font-dir ~/MyFonts
```

Typical output: original PDF font family, whether an exact *name* is installed, optional visually unverified similar names, and language-specific free-font candidates (e.g., Noto Sans TC / Noto Serif TC). Family-name matching is NOT proof that the source face was recovered. For raster JPG/PNG, source fonts cannot be determined reliably without a separate OCR + visual matching pipeline. See `docs/FONTS.md`.

## v0.4 本機 Reconstruction Engines

四個獨立環境的安裝與授權見 [models and licenses](docs/models-and-licenses.md)，架構、限制及恢復契約見 [繁中架構](docs/architecture-v0.4-zh-TW.md)。

```bash
.venv/bin/ali hardware
.venv/bin/ali models list
.venv/bin/python scripts/setup-v04.py segmentation
.venv/bin/ali models fetch sam2-tiny
.venv/bin/ali engine segmentation input.png --out jobs/new-seg --options '{"prompts":"prompts.json"}'
.venv/bin/ali engine ocr input.png --out jobs/new-ocr --options '{"language":"zh-TW","download_models":true}'
.venv/bin/ali font-index models/fonts/catalog.json --out models/fonts/index.json
.venv/bin/ali engine fonts text-crop.png --out jobs/new-fonts --options '{"text":"確認文字","index":"models/fonts/index.json"}'
.venv/bin/ali engine sr input.png --out jobs/new-sr --options '{"strategy":"photo","tile":128,"overlap":16}'
.venv/bin/ali reconstruct input.pdf --out jobs/new-scene --engines ocr --options '{"ocr":{"language":"zh-TW"}}'
.venv/bin/ali scene-adobe jobs/new-scene/scene.json --app indesign --out jobs/new-scene/indesign
```

先安裝各引擎的獨立環境與模型；缺少時返回失敗。新工作目錄避免覆寫，`--resume` 只用於相同來源／選項的工作。舊 `process --upscale 4` 仍是 Lanczos，不是真正 AI；請使用 `engine sr`。

Codex 專案 Skill 位於 `.agents/skills/openlayer-studio/SKILL.md`。Scene Graph 的文字預設隱藏，只有字型與版面核准後才輸出原生文字；真實多頁刊物的整份可編輯版面復原尚未完成。

## 文章編輯稿：使用 story 與欄位串接

文章不能以每一行獨立文字框交付。使用 `ali article-rebuild`，一篇文章一個 story，按欄位串接，段落內自動換行。參考 [文章串接流程](docs/article-flow-zh-TW.md)；舊 `pdf-rebuild` 的逐 span 模式保留作診斷用途。
