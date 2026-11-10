---
name: openlayer-studio
description: Locally inspect and extract JPG, PNG, PDF, PSD graphics into traceable raster assets and SVG, generate operator-reviewable Adobe Photoshop, Illustrator and InDesign scripts, validate manifests. Use for layer analysis, print preflight, vector tracing, Adobe reconstruction and supervised batch workflows.
---

# OpenLayer Studio v0.3 — Codex 專案 Skill

## Operating rules
1. Work only on files the user provided or authorized. Preserve originals; never overwrite originals or existing exports.
2. 在專案根目錄操作。確認 Python >=3.11，以相容 Python 建立 `.venv`，閱讀 `pyproject.toml` 後執行 `.venv/bin/python -m pip install -e ".[test,fonts]"`。先執行 `.venv/bin/ali doctor`；本文 `ali` 均指虛擬環境版本，可先 `source .venv/bin/activate`。不更動系統 Python 或全域設定。
3. Make a new per-job output folder: `jobs/<job-id>`; validate paths, file type and large-image limits.
4. Execute `ali process <input> --out jobs/<job-id>`; then `ali validate jobs/<job-id>/manifest.json`.
5. Make Adobe scripts using `ali adobe jobs/<job-id>/manifest.json --app photoshop|illustrator|indesign --out jobs/<job-id>/adobe-<app>`.
6. Do NOT assert that PSD, AI, INDD files exist merely because JSX scripts were generated. Native creation requires executing scripts in corresponding installed Adobe applications, followed by opening and checking saved output.
7. Flat JPG/PNG only yields a flat layer and approximate SVG vector tracing; it cannot recover original editable layers or original fonts. PDF produces page SVG and text metadata, NOT editable InDesign text boxes. PSD extraction is rasterized per-layer and may differ from Photoshop appearance.
8. When output matters for printing, inspect effective PPI, type, clipping, transparency and placement with human review; obtain proof.
9. Never silently execute downloaded third-party scripts or install background services. No AppleScript automation by default; Adobe JSX runs after explicit user-approved application execution.
10. AI upscaling is NOT implemented in core. `--upscale 2|4` uses Lanczos resize only. Do not say it restores detail.

## Commands
```bash
ali doctor
ali process input.pdf --out jobs/demo --dpi 150
ali validate jobs/demo/manifest.json
ali adobe jobs/demo/manifest.json --app photoshop --out jobs/demo/ps
ali adobe jobs/demo/manifest.json --app illustrator --out jobs/demo/ai
ali adobe jobs/demo/manifest.json --app indesign --out jobs/demo/id
```

## Workflow result
Provide manifest, extracted assets, generated JSX, QA warnings, and actual Adobe output *only if verified on disk*. If app execution unavailable, clearly mark pending and instruct operator to run JSX via File > Scripts (or Scripts panel in InDesign). Read `docs/ARCHITECTURE.md` and `docs/ACCEPTANCE.md` for acceptance gates.

## Multilingual handling (v0.2)
- Accept prompts in the user's language. Default explanations to Traditional Chinese when the user speaks zh-TW.
- Use `ali process INPUT --out JOB --locale zh-TW` (or `auto`, `zh-CN`, `en`, `ja`, `ko`, `fr`, `de`, `es`, `ar`, `he`, `th`, `vi`). Locale is production metadata, NOT a translation or OCR instruction.
- Preserve source UTF-8 text, original fonts and script audit in PDF JSON metadata. Never invent missing translations or reconstruct unreadable text.
- Run font fallback, missing glyph, CJK/line break, vertical-Japanese and Arabic/Hebrew RTL layout checks in Adobe before approving production. Refer to `docs/MULTILINGUAL.md`.
- JPG/PNG have no recoverable text content without OCR; optical recognition and editable text rebuilding remain future integrations.

## Free-font identification / matching (v0.3)
- If asked about fonts in PDFs, run `ali fonts jobs/<job>/manifest.json --out jobs/<job>/font_report.json` after installing `.[fonts]` and completing `ali process`.
- This matches PDF-extracted **font names** against locally installed name tables, and recommends official open-source fonts by project locale. It does NOT visually identify fonts in JPEG/PNG screenshots, prove the matched font is metrically identical, or install font files automatically.
- For raster screenshots, explain that OCR + rendered-glyph candidate comparisons would be required; do not invent font identities. Prefer Noto Sans/Serif CJK and other verified open families only when covering the required script and after checking licenses.
- Never change text, replace fonts or claim native editable type output without Adobe proof. Read `docs/FONTS.md`.

## Codex 操作與驗證
- 此 Skill 搭配完整專案使用；本檔的 `../../..` 為專案根目錄。本文 `docs/`、`jobs/` 等路徑均相對專案根目錄。先閱讀 `README.md`。
- 安裝驗證執行 `pytest -q --basetemp=jobs/pytest-tmp -o cache_dir=jobs/pytest-cache`，再處理範例、驗證 manifest、生成字型報告與 JSX。指令、退出碼、錯誤與修正記於 `reports/`；所有測試產物放於 `jobs/` 或 `reports/`。
- 使用全新 job 目錄；目前程式未全面防止覆寫 manifest、素材與 JSX，重跑前先檢查路徑，必要時換 job id。確認來源 SHA256 未變。
- doctor 只檢查模組可發現性；實際處理及測試才可確認依賴能執行。確認 SVG 非空且可解析，檢視外觀與來源差異。
- 不使用付費 API、不下載未授權商業字型。字型相似名稱不是視覺辨識或字形度量一致的證據。
- 區分 JSX 已生成、Adobe 已執行、原生檔已開啟驗收。先提供手動程序，獲得明確同意才啟動 Adobe 腳本；不得修改原始設計檔。
- 交付 manifest、素材、字型報告、JSX 與 QA 警告；未執行項目標為待驗收。對繁體中文使用者以繁體中文回報。

## v0.4 獨立 Reconstruction Engines

- 舊 `ali process`、`ali adobe` 及 `ali/1` manifest 保持相容。舊 `--upscale` 依然只是 Lanczos；真實 AI 超解析使用 `ali engine sr`，不要混淆兩者。
- 先閱讀 `docs/architecture-v0.4-zh-TW.md`、`docs/models-and-licenses.md`；Adobe 交接閱讀 `docs/adobe-integration.md`。未完成事項見 `docs/architecture-v0.4-zh-TW.md`。
- 模型各引擎使用 `models/envs/` 獨立環境，以 `scripts/setup-v04.py ENGINE` 和 `models/locks/` 安裝；不裝入系統 Python。權重須明確下載並驗證完整性，缺模型或推論失敗不可宣告完成。
- `ali engine segmentation|ocr|fonts|sr INPUT --out NEW_JOB --options JSON`；選項也可使用 `@JSON-file`。`--resume` 僅於同來源與同選項恢復，程式／模型版本改變時建立新 job。
- `ali reconstruct INPUT --out NEW_JOB --engines ocr,segmentation` 產生 scene 與品質報告；PDF 原生文字優先保留。部分引擎失敗時 status=partial、CLI 非零，不得掩蓋。
- SAM 支援 prompt 點、box、polygon corrections；背景 Telea 補全為推估資料，z-order 是啟發式；不聲稱原稿背景或自動還原原始圖層。PyMatting alpha 同樣是估計，複雜透明度尚未驗證。
- OCR 的 language 為指定辨識器，不是已驗證語言分類。字型引擎需要已確認文字與裁切圖，只使用有授權證據的開源候選，輸出 Top 5 與相似度，保留人工選擇。
- Scene Graph 文字預設 hidden；原生文字需 `human_approved`、`approved_font` 並處理底圖重複文字。不要把合成文字 fixture 成功當作使用者刊物完整可編輯重建。
- `ali scene-adobe SCENE --app APP --out NEW_DIR` 只生成 JSX。依原有授權規則執行 Adobe，必須儲存並重新開啟核對，才可宣告該原生檔成功。

## PDF 原生正文工作稿

- `ali pdf-rebuild INPUT --out NEW_JOB --font-map JSON` 可建立多頁 InDesign 重建腳本。只轉換明確映射的原生 PDF 文字，保留特殊字型、重疊效果及原照片／向量；不把這些保留區域宣告為 InDesign 原生可編輯物件。
- 產物是需審閱的工作稿；字型家族／字重映射不是原字型身分證明。以 IDML 文字比對、實際重新開啟的字型／連結／溢排／基線檢查及 PDF 對照驗收。
- Adobe 字型查詢使用有限的明確字型名稱或文件已用字型；避免逐項遍歷整個 InDesign 系統字庫，這曾造成應用程式無回應。若必須強制重啟，先確認使用者授權與未存文件風險。
- Scene Graph 的 Photoshop 匯出已支援從 px／pt 推導 PPI，驗收時仍須實際重新開啟核對實體尺寸。

## 文章與串接欄框

- 使用者要可編輯文章時，使用 `ali article-rebuild` 與明確 article layout；一篇文章一個 story，依欄位串接。不得把逐 PDF span／逐行框當文章編輯稿。
- 去除自動行尾換行、保留段落，閱讀順序按文章與欄位；人物簡介、照片說明與表格記錄分開處理。參考 `docs/article-flow-zh-TW.md`。
- 驗收需實際縮短第一欄並證明下一欄文字流動、全文不變，另核對文章間無混接與無溢排。測試在未儲存副本完成，不改交付檔。
