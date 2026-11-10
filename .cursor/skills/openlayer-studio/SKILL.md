---
name: openlayer-studio
description: Analyze local JPG PNG PDF PSD assets, trace SVG, extract PSD layers, generate Adobe automation scripts with truthful QA.
---

Use the canonical skill in `../../../.claude/skills/openlayer-studio/SKILL.md` and project `README.md`. Follow all safety and verification gates. Run `ali doctor`, `ali process`, `ali validate`, then `ali adobe` as needed. Do not claim native Adobe files exist until opened and validated in the actual Adobe application. No unsupervised install or hidden execution.

Multilingual: read `docs/MULTILINGUAL.md`; pass `--locale zh-TW` or other supported locale. Preserve Unicode; never claim automatic translation/OCR or editable typography.

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
