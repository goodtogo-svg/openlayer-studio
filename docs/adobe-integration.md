# v0.4 Adobe Scene Graph 交接

舊 `ali adobe manifest.json` 維持原功能；新 `ali scene-adobe scene.json --app APP --out NEW_DIR` 消費 ali-scene/1。兩者都只生成腳本，不能單憑 JSX 宣告原生成功。

## 節點對應

| 節點 | Photoshop | Illustrator | InDesign |
|---|---|---|---|
| raster | 獨立像素圖層 | 獨立 placedItem | 獨立圖片框與連結 |
| vector | 須先提供 raster 衍生，不是自動可編輯 shape | 置入向量來源，是否為內部可編輯 paths 需另驗 | 置入圖形；不是原生 path 重建 |
| text | 核准後原生 type layer | 核准後 textFrame | 核准後 textFrame |

只使用 visible nodes，依 z_index 排列。文字需 `human_approved=true` 及 `approved_font`，避免把自動推測當核准；各 Adobe 的字型名稱格式可能不同，需使用該應用程式的實際名稱。PDF 的 bbox、font_size 保留為 pt；OCR polygon 為像素，匯出時換算。

現有匯出器對 raster/vector 採整板置入，適合本次輸出的完整畫布透明物件；裁切物件、任意 transform／旋轉、剪裁路徑與進階文字方向尚未實作。匯入向量不保證為可編輯原生曲線。新增文字前仍須人工處理底圖重複文字，不能只把 hidden 改 true 就當整稿復原。

## 驗收條件

1. 腳本路徑與輸入資產存在，目標為新檔且有拒絕覆寫 guard。
2. 經使用者授權在對應應用程式執行。不得改既有設計文件。
3. 原生檔實際存在、可由應用程式重新開啟。
4. 核對尺寸、圖層／物件數、連結、文字內容與 overflow；另做來源圖比較。
5. 色彩、有效 PPI、字型授權、版面細節及樣張由操作員驗收。儲存與重新開啟不等於商業印刷核准。

## 本次實測

合成 320×240 幾何圖經 SAM 物件分離與推估背景形成 Scene Graph：

- Photoshop 2026：PSD 已儲存、重新開啟，3 個 layers（包含建立文件的透明空圖層）、320×240 px。
- Illustrator 2026：AI 已儲存、重新開啟，2 個 placedItems；不是可編輯曲線還原。
- InDesign 2026：INDD 已儲存、重新開啟，1 頁、2 個圖片框、2 個 links。
- 另一份合成固定字型 fixture：InDesign 原生文字框 1 個，內容「繁體中文 Adobe Layer 2026」，overflows=false。此 fixture 的核准旗標只驗證輸出器契約，不代表已人工核准原刊物字型或版面。

應用程式腳本只建立測試文件，不修改全域安全設定。Illustrator 必須用實際 bundle 路徑載入 AppleScript dictionary；先前只用版本資料夾名稱會出現語法識別錯誤，已記錄並修正呼叫。

**真實多頁刊物尚未完成全稿可編輯重建。** 目前可把 PDF 原生文字 spans 存入 Scene Graph 並跳過不必要的 OCR，但不能拿合成 fixture 的成功替代真實刊物驗收。

## PDF 原生正文重建工作稿（本機增量）

新增 `ali pdf-rebuild INPUT --out NEW_JOB --font-map font-map.json`。這是可審閱工作稿產生器：

1. 從 PDF 讀取文字的原始 baseline、bbox、顏色、字級與方向。
2. 只有明確提供字型映射、無重疊特效及無歧義的文字會轉為原生 textFrames。
3. 在來源的獨立副本移除這些文字，保留原照片、框線與向量；不以 raster inpainting 補字底。
4. 未映射字型、旋轉／透明文字、重疊描邊與陰影保持原 PDF 外觀。若刪除矩形會觸及保留文字，也不自動刪除。
5. 產生單一多頁 INDD 的 JSX，附帶 IDML、PDF 匯出與逐頁 overflow／字寬縮放記錄。

字型映射格式：

```json
{
  "NotoSansTCThin-Regular": {
    "adobe_name": "Noto Sans TC\tRegular",
    "mapping_basis": "same family/weight; release identity unverified",
    "review_required": true
  }
}
```

此途徑沒有宣告字型已獲人工核准；原生文字放在標示需要字型審閱的圖層，屬工作稿。每個 PDF span 是獨立文字框，沒有自動恢復可串流文章。特殊標題與照片位於鎖定的原始向量 PDF 連結，還不能在 InDesign 裡分別修改其內部物件。

`rebuild-indesign.jsx` 使用作用域內的 PDF 匯入頁碼與 redraw 設定，結束時還原。輸出已存在時拒絕覆寫。`scripts/verify-pdf-rebuild.py` 需搭配實際應用程式重新開啟的證據，並比對 IDML 文字 multiset；單憑 ZIP／PDF 存在不算成功。

同次修正：Scene Graph → Photoshop 會依像素與 pt 尺寸計算 PPI，拒絕不一致的非正方像素幾何；文字字級明確以 pt 指定。真實裁切已確認 264×238 px @144 PPI，而不是原先固定72 PPI。
