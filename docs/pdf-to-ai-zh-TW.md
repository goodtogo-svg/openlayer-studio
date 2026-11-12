# PDF 轉 AI 編譯器（`ali pdf-ai`）

把 PDF 編譯成 Illustrator 原生 .ai：每頁一個畫板與同名圖層，文字盡量成為可編輯的原生文字。

## 流程

1. **前端（Python）**：以 PyMuPDF 讀取每頁，轉成中介格式 `compile-ir.json`（schema `ali-pdf-ai/1`）。
   - 文字：以 texttrace 取得字元、glyph id、顏色；以 MuPDF SVG 裝置取得完整文字矩陣（斜體、壓縮、旋轉、鏡像皆精確）。
     同一基線切成一個 run，補回 PDF 只用位移表示的字間空白。
   - 有內嵌字型（TrueType、CFF、CID-keyed CFF）時，以 fontTools 產生字形外框，作為字型未安裝時的後備。
   - `ir` 引擎另外抽出向量路徑（含複合路徑、even-odd、虛線、端點／轉角）與影像（含 SMask、CMYK 轉 RGB、旋轉）。
2. **後端（ExtendScript）**：`build-illustrator.jsx` 讀取同資料夾的 IR，建立文件、畫板與圖層後另存 .ai；拒絕覆寫，輸出 `build-report.json`。工作資料夾可整包搬到其他電腦執行。
3. **驗證**：`verify-illustrator.jsx` 重新開啟 .ai，清點原生物件並逐畫板匯出 PNG；`ali pdf-ai-verify` 與原 PDF 的渲染比較，寫入 `verification.json`。

## 兩種引擎

| 引擎 | 圖形 | 文字 | 適用 |
|---|---|---|---|
| `illustrator-import`（預設） | 由 Illustrator 自己的 PDF 匯入器重現（剪裁遮色片、漸層、透明群組都保留） | 刪除匯入的破碎文字，換成 IR 的整段 run，保留原本的堆疊位置與剪裁群組 | 一般設計稿 |
| `ir` | 完全由 IR 重建；尚未支援剪裁、漸層／shading、軟遮色片、混合模式 | 同上 | 結構單純的 PDF、需要乾淨物件結構時 |

`--text`：`auto`（預設，字型已安裝則為可編輯文字，否則用內嵌字型外框）、`live`（只用可編輯文字，缺字型即報錯）、`outline`（全部外框）、`keep`（只限 illustrator-import，保留 Illustrator 匯入的文字）。`--font-map` 可指定 PDF 字型名稱對應 Illustrator PostScript 名稱。

## 實測（2026-11-12，Illustrator 2027，Mac）

| 測試 | 結果 |
|---|---|
| 合成 2 頁（不同尺寸、內嵌 DejaVu/Inter、旋轉文字、半透明、影像） `ir` | 存檔並重開成功；逐頁像素差 0.6%／0.1%，candidate_pass |
| 合成 2 頁 `illustrator-import` | 第一版跨文件 duplicate 位移錯誤（第 2 頁偏移約 100pt、文字重複）；改用 1pt 標記量測位移後，像素差 0.6%／0.1%，candidate_pass |
| 自強國中橫幅（移除 AI 私有資料後的 PDF，378 個剪裁、38 個 shading） `illustrator-import` | 存檔並重開成功；29 個文字框全為可編輯 NotoSansTC-Black／Arial，像素差 3.9%。差異主要在邊緣反鋸齒，另有兩顆亮點的柔光暈未被 Illustrator 匯入器保留，待人工審閱 |
| 同上 `ir` | 存檔並重開成功；因缺剪裁與 shading，背景與徽章不正確（像素差 57%），屬已知限制 |

驗證結果只是存檔、重開與像素比較的證據，仍須人工審閱字型、色彩與物件結構後才能交付。

## 已知限制

- 環形排列等逐字定位的文字會成為單字元文字框。
- 每個 run 一個點狀文字，不重建段落或串接文章（文章請用 `ali article-rebuild`）。
- 直排文字（wmode=1）目前只能外框化或列為未解決。
- Type1／Type3 內嵌字型無法產生外框；字型未安裝時列入 `unresolved_text`，不會用替代字型。
- 授權：前端使用 PyMuPDF（AGPL）。
